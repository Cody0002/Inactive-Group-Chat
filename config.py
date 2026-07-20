"""Configuration loaded from .env"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" so unrelated keys in .env (e.g. LOG_DIR, read directly by
    # utils.logger) don't crash startup.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Lark app
    LARK_APP_ID: str
    LARK_APP_SECRET: str
    LARK_VERIFICATION_TOKEN: str
    LARK_ENCRYPT_KEY: str = ""
    LARK_DOMAIN: str = "https://open.larksuite.com"

    # Central admin group
    CENTRAL_GROUP_CHAT_ID: str

    # Server (only used to build the public webhook URL, optional)
    SERVER_BASE_URL: str = ""

    # Google Sheets (OAuth 2.0 installed-app flow)
    #   GOOGLE_CREDENTIALS_PATH -> OAuth client secrets file ("installed" client)
    #   GOOGLE_TOKEN_PATH       -> cached authorized-user token (created on first run)
    GOOGLE_CREDENTIALS_PATH: str = "./google_credentials.json"
    GOOGLE_TOKEN_PATH: str = "./google_token.json"
    SPREADSHEET_ID: str

    # Thresholds
    INACTIVITY_THRESHOLD_DAYS: int = 180
    WARN_THRESHOLD_DAYS: int = 90
    # "Approaching inactive" window: groups within this many days of the
    # inactivity threshold appear in the daily digest (e.g. 165-179d for 180/15).
    NEAR_INACTIVE_DAYS: int = 15
    REALERT_COOLDOWN_DAYS: int = 7
    # Weekly report section B: groups quiet for more than this many days.
    WEEKLY_INACTIVE_DAYS: int = 60

    # Group size split: <= this many members at creation = "Personal", else "Team".
    PERSONAL_GROUP_MAX_MEMBERS: int = 3

    # Daily digest schedule (local time). Bangkok = UTC+7, no DST.
    # One report a day — every day EXCEPT Friday, when the weekly report goes
    # out at this same hour instead, so the two can never collide.
    DAILY_REPORT_TZ: str = "Asia/Bangkok"
    DAILY_REPORT_HOUR: int = 17

    # New groups are logged silently and surfaced in the daily digest.
    # Set true to also push a card the moment each group is created.
    NOTIFY_ON_NEW_GROUP: bool = False

    # Audit-log activity fallback
    AUDIT_ACTIVITY_ENABLED: bool = True
    AUDIT_ACTIVITY_LOOKBACK_DAYS: int = 1
    AUDIT_ACTIVITY_EVENTS: str = (
        "im_chat_uploadfile,im_send_link,im_chat_editimage,im_chat_withdraw,"
        "im_addtochat,im_deletefromchat,im_add_chatadmin,im_delete_chatadmin,"
        "im_forward_chatadmin,im_admin_no_restrict_ctrl,im_join_chat,im_quit_chat,"
        "im_chat_pin_create,im_chat_pin_update,im_chat_pin_update_permission,"
        "im_chat_pin_delete,im_chat_pin_stick,im_chat_pin_unstick,"
        "im_export_chat_chatter,im_forward_file,im_savetospace,"
        # Content consumption — also proves the group is alive
        "im_chat_previewfile,im_copy_image,im_open_link,im_download,"
        "im_download_file,im_download_image,im_download_video,"
        "im_load_file_to_local,im_snaphot,im_screencap,im_ocr,"
        "im_read_doc,im_download_doc,im_open_with_3rdApp"
    )


settings = Settings()
