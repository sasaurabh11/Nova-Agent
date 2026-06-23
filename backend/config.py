from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")


def _getf(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _geti(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


PRICE_TABLE: dict[str, tuple[float, float]] = {  # model -> (in_per_1m, out_per_1m)
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-1.5-flash": (0.075, 0.30),
}
_DEFAULT_PRICE = (0.10, 0.40)


def price_for(model: str) -> tuple[float, float]:
    return PRICE_TABLE.get(model, _DEFAULT_PRICE)


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    uncertain_threshold: float
    auto_approve_threshold: float
    max_llm_retries: int
    llm_timeout_s: int
    shipment_budget_s: int
    db_path: str
    imap_host: str
    imap_port: int
    imap_folder: str
    email_user: str
    email_password: str
    smtp_host: str
    smtp_port: int
    default_customer_id: str
    emails_dir: str          # where fetched attachments are stored
    poll_interval_s: int     # how often to poll IMAP for new mail
    imap_search: str         # IMAP SEARCH override (blank -> UNSEEN since baseline UID)
    max_fetch_per_poll: int  # cap on emails fetched per poll
    max_pipeline_attempts: int  # bounded retries before giving up to needs_review
    retry_backoff_s: int     # base delay before re-enqueueing a failed shipment

    @property
    def db_abspath(self) -> str:
        p = Path(self.db_path)
        if not p.is_absolute():
            p = _REPO_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    @property
    def emails_abspath(self) -> str:
        p = Path(self.emails_dir)
        if not p.is_absolute():
            p = _REPO_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    @property
    def email_configured(self) -> bool:
        return bool(self.email_user and self.email_password)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config(
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        uncertain_threshold=_getf("UNCERTAIN_THRESHOLD", 0.70),
        auto_approve_threshold=_getf("AUTO_APPROVE_THRESHOLD", 0.85),
        max_llm_retries=_geti("MAX_LLM_RETRIES", 2),
        llm_timeout_s=_geti("LLM_TIMEOUT_S", 60),
        shipment_budget_s=_geti("SHIPMENT_BUDGET_S", 180),
        db_path=os.getenv("DB_PATH", "./data/nova.db"),
        imap_host=os.getenv("IMAP_HOST", "imap.gmail.com"),
        imap_port=_geti("IMAP_PORT", 993),
        imap_folder=os.getenv("IMAP_FOLDER", "INBOX"),
        email_user=os.getenv("EMAIL_USER", ""),
        email_password=os.getenv("EMAIL_PASSWORD", ""),
        smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_geti("SMTP_PORT", 587),
        default_customer_id=os.getenv("DEFAULT_CUSTOMER_ID", "cust_acme"),
        emails_dir=os.getenv("EMAILS_DIR", "./data/emails"),
        poll_interval_s=_geti("POLL_INTERVAL_S", 15),
        imap_search=os.getenv("IMAP_SEARCH", ""),
        max_fetch_per_poll=_geti("MAX_FETCH_PER_POLL", 10),
        max_pipeline_attempts=_geti("MAX_PIPELINE_ATTEMPTS", 3),
        retry_backoff_s=_geti("RETRY_BACKOFF_S", 10),
    )
