import os

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.models import SpoterWebhookPayload

app = FastAPI(title="MISS")


def verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)):
    expected = os.environ.get("WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WEBHOOK_SECRET not configured",
        )
    if x_webhook_secret != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@app.post(
    "/v1/spoter/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_webhook_secret)],
)
async def spoter_webhook(payload: SpoterWebhookPayload):
    return {"status": "accepted"}
