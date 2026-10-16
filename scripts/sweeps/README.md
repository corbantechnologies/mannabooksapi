# API contract sweeps

Static checks that catch the bugs that broke screens during staging testing: the frontend calling
routes that don't exist, sending fields the API ignores, or reading fields the API never returns; and
backend code using model fields that don't exist. Run them after changing an endpoint or a screen.

Run from the `mannabooksapi` folder (paths assume the two repos sit side by side):

```bash
python scripts/sweeps/sweep_backend.py /tmp/backend_routes.json
```

```bash
python scripts/sweeps/sweep_frontend.py ../mannabooks/src /tmp/frontend_calls.json
```

```bash
python scripts/sweeps/sweep_check.py /tmp/backend_routes.json /tmp/frontend_calls.json
```

```bash
python scripts/sweeps/sweep_models.py
```

`sweep_check.py` groups findings into missing routes, wrong methods, ignored request fields, missing
required query parameters, response fields not returned, and ignored query parameters. Response-field
findings are heuristic: many have a working fallback (`res.id || res.documentId`), so review them by hand.
