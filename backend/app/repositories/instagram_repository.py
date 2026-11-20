"""Instagram account repository."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session
from app.models.models import InstagramAccount, InstagramAccountStatus
from app.services.instagram.token_manager import TokenManager

# Stable identity for simulated demo operations
DEMO_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USERNAME = "adoraxe_demo"
DEMO_USER_ID = "demo_instagram_user_001"


class InstagramRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_demo_account(self) -> InstagramAccount:
        """Get or create the stable demo account row in database."""
        account = self.db.query(InstagramAccount).filter(InstagramAccount.id == DEMO_ACCOUNT_ID).first()
        if not account:
            account = InstagramAccount(
                id=DEMO_ACCOUNT_ID,
                instagram_user_id=DEMO_USER_ID,
                username=DEMO_USERNAME,
                full_name="Adoraxe Demo Creator",
                account_type="CREATOR",
                status=InstagramAccountStatus.CONNECTED,
                connected_at=datetime.now(timezone.utc),
            )
            self.db.add(account)
            self.db.commit()
            self.db.refresh(account)
        return account

    def get_effective_account(self, is_demo: bool = False) -> Optional[InstagramAccount]:
        """
        Returns the active connected Instagram account.
        In demo mode, falls back to the stable demo account.
        """
        active = self.get_active_account()
        if active:
            return active
        if is_demo:
            return self.get_or_create_demo_account()
        return None

    def get_active_account(self) -> Optional[InstagramAccount]:
        return self.db.query(InstagramAccount).filter(
            InstagramAccount.status == InstagramAccountStatus.CONNECTED,
            InstagramAccount.id != DEMO_ACCOUNT_ID,
        ).order_by(InstagramAccount.updated_at.desc(), InstagramAccount.connected_at.desc()).first()

    def get_account_by_id(self, account_id: str) -> Optional[InstagramAccount]:
        return self.db.query(InstagramAccount).filter(InstagramAccount.id == account_id).first()

    def list_all_accounts(self) -> list[InstagramAccount]:
        return self.db.query(InstagramAccount).order_by(
            InstagramAccount.updated_at.desc(),
            InstagramAccount.connected_at.desc(),
        ).all()

    def set_active_account(self, account_id: str) -> Optional[InstagramAccount]:
        acc = self.db.query(InstagramAccount).filter(InstagramAccount.id == account_id).first()
        if not acc:
            return None
        acc.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(acc)
        return acc

    def disconnect_all(self):
        accounts = self.db.query(InstagramAccount).all()
        for a in accounts:
            a.status = InstagramAccountStatus.NOT_CONNECTED
            a.encrypted_access_token = None
        self.db.commit()

    def upsert_account(self, account_data: dict) -> InstagramAccount:
        user_data = account_data.get("user_data", {})
        ig_user_id = str(user_data.get("id") or account_data.get("user_id") or "")
        username = user_data.get("username") or user_data.get("name") or "unknown"
        full_name = user_data.get("name")
        account_type = user_data.get("account_type", "CREATOR")
        profile_picture_url = user_data.get("profile_picture_url")
        followers_count = user_data.get("followers_count")
        expires_in = account_data.get("expires_in")

        # Encrypt token before storage
        token_mgr = TokenManager()
        encrypted_token = token_mgr.encrypt_token(account_data["access_token"])

        existing = self.db.query(InstagramAccount).filter(
            InstagramAccount.instagram_user_id == ig_user_id
        ).first()

        expires_at = None
        if expires_in and isinstance(expires_in, (int, float)) and expires_in > 0:
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        if existing:
            existing.username = username
            if full_name:
                existing.full_name = full_name
            if account_type:
                existing.account_type = account_type
            if profile_picture_url:
                existing.profile_picture_url = profile_picture_url
            if followers_count is not None:
                existing.followers_count = followers_count
            existing.encrypted_access_token = encrypted_token
            existing.token_expires_at = expires_at
            existing.status = InstagramAccountStatus.CONNECTED
            self.db.commit()
            self.db.refresh(existing)
            return existing

        account = InstagramAccount(
            instagram_user_id=ig_user_id,
            username=username,
            full_name=full_name,
            account_type=account_type,
            profile_picture_url=profile_picture_url,
            followers_count=followers_count,
            encrypted_access_token=encrypted_token,
            token_expires_at=expires_at,
            status=InstagramAccountStatus.CONNECTED,
            connected_at=datetime.utcnow(),
        )
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)
        return account

    def update_account_profile(
        self,
        account_id: str,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        profile_picture_url: Optional[str] = None,
        followers_count: Optional[int] = None,
        account_type: Optional[str] = None,
    ) -> Optional[InstagramAccount]:
        """Safely update account profile fields from fresh Graph API verification."""
        acc = self.db.query(InstagramAccount).filter(InstagramAccount.id == account_id).first()
        if not acc:
            return None
        if username:
            acc.username = username
        if full_name:
            acc.full_name = full_name
        if profile_picture_url is not None:
            acc.profile_picture_url = profile_picture_url
        if followers_count is not None:
            acc.followers_count = followers_count
        if account_type:
            acc.account_type = account_type
        self.db.commit()
        self.db.refresh(acc)
        return acc
