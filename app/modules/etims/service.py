import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.document import Document
from app.models.etims import EtimsConfig, EtimsSequence, EtimsTransmission
from app.models.item import Product
from app.modules.etims.crypto import encrypt_cmc_key, decrypt_cmc_key
from app.modules.etims.client import KraEtimsClient
from app.modules.etims.schemas import (
    EtimsInitRequest,
    EtimsItemPayload,
    EtimsResponseEnvelope,
    EtimsSalesTransmissionRequest,
)
from app.core.clock import now_nairobi
from app.core.datetime_utils import utcnow


async def register_etims_device(
    db: AsyncSession,
    req: EtimsInitRequest,
) -> Dict[str, Any]:
    """Initialize Virtual Device with KRA OSCU Gateway and persist credentials."""
    client = KraEtimsClient()

    # 1. Handshake with KRA OSCU
    kra_res = await client.initialize_device(
        tin=req.kra_pin,
        bhf_id=req.branch_id,
        dvc_srl_no=req.device_serial,
        cmc_key=req.communication_key,
    )

    if kra_res.get("resultCd") != "000":
        return {
            "success": False,
            "error": kra_res.get("resultMsg", "Failed to handshake with KRA gateway"),
        }

    info = kra_res.get("data", {}).get("info", {})
    sdc_id = info.get("sdcId")
    mrc_no = info.get("mrcNo")

    # 2. Encrypt & Save Config
    enc_cmc = encrypt_cmc_key(req.communication_key)

    q = select(EtimsConfig).where(EtimsConfig.shop_id == req.shop_id)
    res = await db.execute(q)
    config = res.scalar_one_or_none()

    if not config:
        config = EtimsConfig(
            shop_id=req.shop_id,
            kra_pin=req.kra_pin,
            branch_id=req.branch_id,
            device_serial=req.device_serial,
            communication_key_encrypted=enc_cmc,
            sdc_id=sdc_id,
            mrc_no=mrc_no,
            environment=req.environment,
            is_active=True,
            last_sync_at=utcnow(),
        )
        db.add(config)
    else:
        config.kra_pin = req.kra_pin
        config.branch_id = req.branch_id
        config.device_serial = req.device_serial
        config.communication_key_encrypted = enc_cmc
        config.sdc_id = sdc_id
        config.mrc_no = mrc_no
        config.environment = req.environment
        config.is_active = True
        config.last_sync_at = utcnow()

    await db.commit()

    return {
        "success": True,
        "sdcId": sdc_id,
        "mrcNo": mrc_no,
        "taxpayerName": info.get("taxprNm"),
    }


CENT = Decimal("0.01")
# Same codes the header totals below use (taxAmtA carries the 16% VAT).
ETIMS_TAX_CODES = {"V_16": "A", "V_0": "B", "EXEMPT": "C"}
VAT_RATES = {"V_16": Decimal("0.16")}


async def sales_items_for_document(db: AsyncSession, doc: Document) -> List[EtimsItemPayload]:
    """
    eTIMS lines for a sales document, VAT inclusive as KRA expects (taxblAmt = splyAmt - dcAmt).

    A loyalty redemption or member discount is recorded on the document, not on its lines, so it is
    spread over the lines by value here: each line gets its dcAmt / dcRt, and its taxable amount and VAT
    are after the discount. The lines add up exactly to the document's tax_amount and grand_total.
    """
    items = list(doc.items or [])
    gross = [Decimal(str(it.quantity)) * Decimal(str(it.unit_price)) + Decimal(str(it.tax_amount or 0)) for it in items]
    total_gross = sum(gross, Decimal(0))
    discount = Decimal(0)
    if Decimal(str(doc.loyalty_discount_amount or 0)) > 0:
        discount = max(Decimal(0), total_gross - Decimal(str(doc.grand_total or 0)))

    shares, left = [], discount
    for i, g in enumerate(gross):
        share = left if i == len(gross) - 1 else (discount * g / total_gross).quantize(CENT, ROUND_HALF_UP) if total_gross else Decimal(0)
        share = min(share, g)
        left -= share
        shares.append(share)
    taxes = []
    for it, share in zip(items, shares):
        rate = VAT_RATES.get(it.tax_type, Decimal(0))
        taxes.append(Decimal(str(it.tax_amount or 0)) - (share * rate / (1 + rate)).quantize(CENT, ROUND_HALF_UP))
    # Rounding: make the lines' VAT match the document's exactly, on the last taxed line.
    drift = Decimal(str(doc.tax_amount or 0)) - sum(taxes, Decimal(0))
    taxed = [i for i, it in enumerate(items) if it.tax_type in VAT_RATES]
    if drift and taxed:
        taxes[taxed[-1]] += drift

    product_ids = [it.product_id for it in items if it.product_id]
    skus = {}
    if product_ids:
        skus = dict((await db.execute(select(Product.id, Product.sku).where(Product.id.in_(product_ids)))).all())

    out = []
    for seq, (it, g, share, tax) in enumerate(zip(items, gross, shares, taxes), start=1):
        qty = Decimal(str(it.quantity))
        taxable = g - share
        out.append(
            EtimsItemPayload(
                itemSeq=seq,
                itemCd=(skus.get(it.product_id) or f"MB{seq:05d}")[:20],
                itemNm=it.description[:200],
                qty=float(qty),
                prc=float((g / qty).quantize(CENT, ROUND_HALF_UP)) if qty else 0.0,
                splyAmt=float(g),
                dcRt=float((share / g * 100).quantize(CENT, ROUND_HALF_UP)) if g else 0.0,
                dcAmt=float(share),
                taxTyCd=ETIMS_TAX_CODES.get(it.tax_type, "A"),
                taxblAmt=float(taxable),
                taxAmt=float(tax),
                totAmt=float(taxable),
            )
        )
    return out


async def allocate_next_invoice_number(db: AsyncSession, shop_id: uuid.UUID, branch_id: str = "00") -> int:
    """Row-locked monotonic sequence allocation per shop and branch."""
    q = (
        select(EtimsSequence)
        .where(EtimsSequence.shop_id == shop_id, EtimsSequence.branch_id == branch_id)
        .with_for_update()
    )
    res = await db.execute(q)
    seq = res.scalar_one_or_none()

    if not seq:
        seq = EtimsSequence(shop_id=shop_id, branch_id=branch_id, last_sequence_number=1)
        db.add(seq)
        await db.flush()
        return 1

    seq.last_sequence_number += 1
    await db.flush()
    return seq.last_sequence_number


async def transmit_fiscal_invoice(
    db: AsyncSession,
    req: EtimsSalesTransmissionRequest,
) -> EtimsResponseEnvelope:
    """Outbox execution: allocate locked sequence, save transmission record, transmit to KRA."""
    # 0. System-wide Owner Master Switch Check
    from app.core.config import get_settings
    settings = get_settings()
    if not settings.ENABLE_KRA_ETIMS_GLOBAL:
        return EtimsResponseEnvelope(
            success=False,
            status="SKIPPED",
            error="eTIMS integration is globally disabled at the platform level.",
        )

    q_cfg = select(EtimsConfig).where(EtimsConfig.shop_id == req.shop_id, EtimsConfig.is_active == True)
    res_cfg = await db.execute(q_cfg)
    config = res_cfg.scalar_one_or_none()

    if not config:
        return EtimsResponseEnvelope(
            success=False,
            status="SKIPPED",
            error="Workspace has not activated KRA eTIMS integration.",
        )

    if not req.items:
        doc = (
            await db.execute(
                select(Document)
                .where(Document.id == req.document_id, Document.shop_id == req.shop_id)
                .options(selectinload(Document.items))
            )
        ).scalar_one_or_none()
        if not doc:
            return EtimsResponseEnvelope(success=False, status="FAILED", error="Document not found in this workspace.")
        req.items = await sales_items_for_document(db, doc)

    cmc_key = decrypt_cmc_key(config.communication_key_encrypted)

    # 1. Allocate next invoice number with lock
    invc_no = await allocate_next_invoice_number(db, req.shop_id, config.branch_id)

    # 2. Compute aggregate tax breakdown
    tot_taxbl = sum(i.taxblAmt for i in req.items)
    tot_tax = sum(i.taxAmt for i in req.items)
    tot_amt = sum(i.totAmt for i in req.items)

    now_n = now_nairobi()
    cfm_dt = now_n.strftime("%Y%m%d%H%M%S")
    sales_dt = now_n.strftime("%Y%m%d")

    payload = {
        "tin": config.kra_pin,
        "bhfId": config.branch_id,
        "invcNo": invc_no,
        "orgInvcNo": 0,
        "custTin": req.customer_tin or "",
        "custNm": req.customer_name,
        "salesTyCd": req.sales_type_code,
        "rcptTyCd": req.receipt_type_code,
        "pmtTyCd": req.payment_type_code,
        "salesSttsCd": "02",
        "cfmDt": cfm_dt,
        "salesDt": sales_dt,
        "stockRlsDt": cfm_dt,
        "totItemCnt": len(req.items),
        "taxblAmtA": sum(i.taxblAmt for i in req.items if i.taxTyCd == "A"),
        "taxblAmtB": sum(i.taxblAmt for i in req.items if i.taxTyCd == "B"),
        "taxblAmtC": sum(i.taxblAmt for i in req.items if i.taxTyCd == "C"),
        "taxblAmtD": sum(i.taxblAmt for i in req.items if i.taxTyCd == "D"),
        "taxblAmtE": sum(i.taxblAmt for i in req.items if i.taxTyCd == "E"),
        "taxAmtA": sum(i.taxAmt for i in req.items if i.taxTyCd == "A"),
        "taxAmtB": 0.0,
        "taxAmtC": 0.0,
        "taxAmtD": 0.0,
        "taxAmtE": sum(i.taxAmt for i in req.items if i.taxTyCd == "E"),
        "totTaxblAmt": tot_taxbl,
        "totTaxAmt": tot_tax,
        "totAmt": tot_amt,
        "prchrAcptcYn": "Y",
        "regrId": "MANNA_SYSTEM",
        "regrNm": "MannaBooks Engine",
        "modrId": "MANNA_SYSTEM",
        "modrNm": "MannaBooks Engine",
        "itemList": [item.model_dump() for item in req.items],
    }

    # 3. Create Outbox Transmission record inside unit of work
    tx = EtimsTransmission(
        shop_id=req.shop_id,
        document_id=req.document_id,
        request_type="SALES_INVOICE",
        invoice_number=invc_no,
        request_payload=payload,
        status="QUEUED",
    )
    db.add(tx)
    await db.commit()

    # 4. Attempt immediate dispatch (≤ 2.5s)
    client = KraEtimsClient()
    try:
        kra_res = await client.save_sales_transaction(payload, cmc_key)
        if kra_res.get("resultCd") == "000":
            data = kra_res.get("data", {})
            tx.status = "FISCALIZED"
            tx.status_code = "000"
            tx.response_payload = kra_res
            await db.commit()

            mrc_no = data.get("mrcNo") or config.mrc_no
            cu_inv_no = f"{mrc_no}-{invc_no:08d}" if mrc_no else str(invc_no)

            return EtimsResponseEnvelope(
                success=True,
                status="FISCALIZED",
                invoice_number=invc_no,
                cu_serial=mrc_no,
                cu_invoice_number=cu_inv_no,
                qr_code_url=data.get("qrCode"),
            )
        else:
            tx.status = "FAILED"
            tx.status_code = kra_res.get("resultCd")
            tx.error_message = kra_res.get("resultMsg")
            tx.response_payload = kra_res
            await db.commit()

            return EtimsResponseEnvelope(
                success=False,
                status="QUEUED",  # Kept queued for background worker retry
                invoice_number=invc_no,
                error=kra_res.get("resultMsg"),
            )
    except Exception as exc:
        tx.status = "QUEUED"
        tx.error_message = str(exc)
        await db.commit()
        return EtimsResponseEnvelope(
            success=False,
            status="QUEUED",
            invoice_number=invc_no,
            error="Network timeout; transaction scheduled in offline retry queue.",
        )
