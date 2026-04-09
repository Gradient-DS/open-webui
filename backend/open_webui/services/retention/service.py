"""Data retention service — automated cleanup based on configurable TTL."""

import logging
import time
from dataclasses import dataclass, field

from open_webui.models.users import Users
from open_webui.models.chats import Chats
from open_webui.models.knowledge import Knowledges
from open_webui.services.retention.config import (
    get_effective_ttl_days,
    get_cutoff_timestamp,
    is_retention_enabled,
)

log = logging.getLogger(__name__)


@dataclass
class RetentionReport:
    users_deleted: int = 0
    users_archived: int = 0
    chats_deleted: int = 0
    knowledge_deleted: int = 0
    warnings_sent: int = 0
    errors: list[str] = field(default_factory=list)


class DataRetentionService:
    """Orchestrates automated data cleanup based on TTL configuration."""

    @staticmethod
    async def run_cleanup(
        app,
        master_ttl: int,
        user_inactivity_ttl: int,
        chat_ttl: int,
        knowledge_ttl: int,
        warning_days: int = 30,
        enable_warning_email: bool = False,
        enable_archival: bool = True,
        archive_retention_days: int = 1095,
    ) -> RetentionReport:
        """Run all retention cleanup phases.

        Args:
            app: FastAPI app instance (for email sending)
            master_ttl: DATA_RETENTION_TTL_DAYS (0 = disabled)
            user_inactivity_ttl: USER_INACTIVITY_TTL_DAYS
            chat_ttl: CHAT_RETENTION_TTL_DAYS (0 = inherit master)
            knowledge_ttl: KNOWLEDGE_RETENTION_TTL_DAYS (0 = inherit master)
            warning_days: DATA_RETENTION_WARNING_DAYS
            enable_warning_email: ENABLE_RETENTION_WARNING_EMAIL
            enable_archival: whether to archive users before deletion
            archive_retention_days: retention for auto-created archives
        """
        report = RetentionReport()

        if not is_retention_enabled(master_ttl):
            return report

        effective_user_ttl = get_effective_ttl_days(master_ttl, user_inactivity_ttl)

        # Phase 0: Send warning emails to users approaching inactivity TTL
        if effective_user_ttl > 0 and warning_days > 0 and enable_warning_email:
            await DataRetentionService._send_warning_emails(app, effective_user_ttl, warning_days, report)

        # Phase 1: Inactive users
        if effective_user_ttl > 0:
            DataRetentionService._cleanup_inactive_users(
                effective_user_ttl, enable_archival, archive_retention_days, report
            )

        # Phase 2: Stale chats (only for still-active users)
        effective_chat_ttl = get_effective_ttl_days(master_ttl, chat_ttl)
        if effective_chat_ttl > 0:
            DataRetentionService._cleanup_stale_chats(effective_chat_ttl, report)

        # Phase 3: Stale knowledge bases (only local type, active users)
        effective_kb_ttl = get_effective_ttl_days(master_ttl, knowledge_ttl)
        if effective_kb_ttl > 0:
            DataRetentionService._cleanup_stale_knowledge(effective_kb_ttl, report)

        return report

    @staticmethod
    async def _send_warning_emails(
        app,
        user_ttl_days: int,
        warning_days: int,
        report: RetentionReport,
    ) -> None:
        """Phase 0: Send warning emails to users approaching inactivity deletion.

        Finds users whose last_active_at is in the warning window:
        (ttl - warning_days) < inactive_days < ttl

        Tracks sent warnings via user.info['retention_warning_sent_at'] to avoid
        re-sending. A warning is re-sent only if the user logged in since the last
        warning (which resets last_active_at) and went inactive again.
        """
        from open_webui.services.email.graph_mail_client import (
            send_mail,
            render_retention_warning_subject,
            render_retention_warning_email,
        )
        from open_webui.config import DEFAULT_LOCALE, WEBUI_URL

        now = int(time.time())
        # Users inactive longer than (ttl - warning) days but not yet at ttl
        warning_cutoff = now - ((user_ttl_days - warning_days) * 86400)
        deletion_cutoff = now - (user_ttl_days * 86400)

        # Get users in the warning window
        users_approaching = Users.get_inactive_users(
            inactive_since=warning_cutoff,
            limit=100,
            exclude_roles=['admin'],
        )

        locale = str(DEFAULT_LOCALE) if DEFAULT_LOCALE else 'en'
        login_url = str(WEBUI_URL).rstrip('/') + '/auth' if WEBUI_URL else ''

        for user in users_approaching:
            # Skip users already past the deletion cutoff (they'll be deleted)
            if user.last_active_at <= deletion_cutoff:
                continue

            # Check if we already sent a warning for this inactivity period
            info = user.info or {}
            warning_sent_at = info.get('retention_warning_sent_at', 0)
            if warning_sent_at > user.last_active_at:
                # Already warned since their last activity — skip
                continue

            # Calculate approximate days remaining
            seconds_until_deletion = (user.last_active_at + (user_ttl_days * 86400)) - now
            days_remaining = max(1, seconds_until_deletion // 86400)

            try:
                subject = render_retention_warning_subject(
                    days_remaining=days_remaining,
                    locale=locale,
                )
                html_body = render_retention_warning_email(
                    login_url=login_url or 'your platform',
                    days_remaining=days_remaining,
                    locale=locale,
                )

                await send_mail(
                    app=app,
                    to_address=user.email,
                    subject=subject,
                    html_body=html_body,
                )

                # Mark warning as sent in user info
                info['retention_warning_sent_at'] = now
                Users.update_user_by_id(user.id, {'info': info})

                report.warnings_sent += 1
                log.info(f'Retention: sent warning email to user {user.id} ({days_remaining} days until deletion)')
            except Exception as e:
                error_msg = f'Retention: failed to send warning to user {user.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

        if report.warnings_sent > 0:
            log.info(f'Retention: sent {report.warnings_sent} warning emails')

    @staticmethod
    def _cleanup_inactive_users(
        ttl_days: int,
        enable_archival: bool,
        archive_retention_days: int,
        report: RetentionReport,
    ) -> None:
        """Phase 1: Find and delete inactive users."""
        from open_webui.services.deletion.service import DeletionService
        from open_webui.services.archival.service import ArchiveService

        cutoff = get_cutoff_timestamp(ttl_days)
        users = Users.get_inactive_users(
            inactive_since=cutoff,
            limit=50,
            exclude_roles=['admin'],  # Never auto-delete admins
        )

        for user in users:
            try:
                # Archive before deletion if enabled
                if enable_archival:
                    try:
                        result = ArchiveService.create_archive(
                            user_id=user.id,
                            archived_by='system:retention',
                            reason=f'Automated retention cleanup — user inactive for {ttl_days}+ days',
                            retention_days=archive_retention_days,
                        )
                        if result.success:
                            report.users_archived += 1
                            log.info(
                                f'Retention: archived user {user.id} '
                                f'before deletion (inactive since {user.last_active_at})'
                            )
                    except Exception as e:
                        log.warning(f'Retention: failed to archive user {user.id}, proceeding with deletion: {e}')

                # Delete user (cascade: soft-delete chats/KBs, hard-delete rest)
                deletion_report = DeletionService.delete_user(user.id)
                if not deletion_report.errors:
                    report.users_deleted += 1
                    log.info(f'Retention: deleted inactive user {user.id} — last active: {user.last_active_at}')
                else:
                    error_msg = f'Retention: failed to delete user {user.id}: {deletion_report.errors}'
                    log.error(error_msg)
                    report.errors.append(error_msg)

            except Exception as e:
                error_msg = f'Retention: error processing user {user.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

    @staticmethod
    def _cleanup_stale_chats(ttl_days: int, report: RetentionReport) -> None:
        """Phase 2: Soft-delete stale chats. Cleanup worker handles cascade."""
        cutoff = get_cutoff_timestamp(ttl_days)
        stale_chats = Chats.get_stale_chats(stale_before=cutoff, limit=500)

        for chat in stale_chats:
            try:
                Chats.soft_delete_by_id(chat.id)
                report.chats_deleted += 1
            except Exception as e:
                error_msg = f'Retention: failed to soft-delete chat {chat.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

        if stale_chats:
            log.info(f'Retention: soft-deleted {report.chats_deleted} stale chats (older than {ttl_days} days)')

    @staticmethod
    def _cleanup_stale_knowledge(ttl_days: int, report: RetentionReport) -> None:
        """Phase 3: Soft-delete stale local KBs. Cleanup worker handles cascade."""
        cutoff = get_cutoff_timestamp(ttl_days)
        stale_kbs = Knowledges.get_stale_knowledge(stale_before=cutoff, limit=50)

        for kb in stale_kbs:
            try:
                Knowledges.soft_delete_by_id(kb.id)
                report.knowledge_deleted += 1
            except Exception as e:
                error_msg = f'Retention: failed to soft-delete KB {kb.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

        if stale_kbs:
            log.info(
                f'Retention: soft-deleted {report.knowledge_deleted} stale knowledge bases (older than {ttl_days} days)'
            )
