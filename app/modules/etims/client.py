from typing import Optional, Dict, Any
import httpx
from app.core.config import get_settings

settings = get_settings()


class KraEtimsClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 2.5):
        self.base_url = (base_url or settings.KRA_ETIMS_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.mock_mode = settings.KRA_ETIMS_MOCK_MODE

    async def initialize_device(self, tin: str, bhf_id: str, dvc_srl_no: str, cmc_key: str) -> Dict[str, Any]:
        """Handshake with KRA OSCU /selectInitInfo endpoint."""
        if self.mock_mode:
            clean_tin = tin.upper().strip()
            clean_bhf = bhf_id.strip()
            clean_srl = dvc_srl_no.strip()
            return {
                "resultCd": "000",
                "resultMsg": "Successful (MOCK SIMULATION)",
                "data": {
                    "info": {
                        "tin": clean_tin,
                        "bhfId": clean_bhf,
                        "dvcSrlNo": clean_srl,
                        "sdcId": f"SDC{clean_srl[:6]}",
                        "mrcNo": f"KRA{clean_tin[-4:]}{clean_bhf}",
                        "taxprNm": "VERIFIED TAXPAYER ENTERPRISE",
                    }
                },
            }

        url = f"{self.base_url}/selectInitInfo"
        headers = {
            "Content-Type": "application/json",
            "cmcKey": cmc_key,
        }
        payload = {
            "tin": tin.upper().strip(),
            "bhfId": bhf_id.strip(),
            "dvcSrlNo": dvc_srl_no.strip(),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def save_sales_transaction(self, payload: Dict[str, Any], cmc_key: str) -> Dict[str, Any]:
        """Send invoice / sales receipt payload to KRA /saveTrnsSalesOsdc."""
        if self.mock_mode:
            invc_no = payload.get("invcNo", 1)
            tin = payload.get("tin", "P000000000A")
            bhf_id = payload.get("bhfId", "00")
            mrc_no = f"KRA{tin[-4:]}{bhf_id}"
            cu_invc = f"{mrc_no}-{invc_no:08d}"
            return {
                "resultCd": "000",
                "resultMsg": "Successful (MOCK SIMULATION)",
                "data": {
                    "mrcNo": mrc_no,
                    "curRcptNo": invc_no,
                    "totRcptNo": invc_no,
                    "rcptSign": f"KRA_SIGN_{invc_no:08d}",
                    "qrCode": f"https://etims.kra.go.ke/common/link/etims/receipt/index.html?qrCode={cu_invc}",
                },
            }

        url = f"{self.base_url}/saveTrnsSalesOsdc"
        headers = {
            "Content-Type": "application/json",
            "cmcKey": cmc_key,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
