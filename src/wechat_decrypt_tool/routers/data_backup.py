"""Local application-owned records: download and restore missing live data."""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .ai import local_only
from ..application_accounts import application_account_name as account_name, application_account_work as account_work
from ..ai.service import get_ai_service
from ..application_backup import ApplicationBackupError, ApplicationBackupConflict, export_backup, restore_backup, apply_backup
from ..library_service import LibraryService


router = APIRouter(prefix='/api/data-backup', dependencies=[Depends(local_only)])


class BackupDownload(FileResponse):
    def __init__(self, path, temporary):
        super().__init__(path, filename='xwechat-application-data.zip', media_type='application/zip')
        self.temporary = temporary

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.temporary.cleanup()


@router.post('/export')
def export(account: str = Query(min_length=1, pattern=r'\S')):
    owner = account_name(account)
    temporary = TemporaryDirectory(prefix='xwechat-data-backup-')
    try:
        destination = Path(temporary.name) / 'application-data.zip'
        # A synchronous route retains its worker and lease until copying ends.
        # The completed ZIP is independent of the account while downloading.
        with account_work(owner):
            export_backup(LibraryService(get_ai_service(), account_name), owner, destination)
        return BackupDownload(destination, temporary)
    except BaseException as error:
        temporary.cleanup()
        if isinstance(error, ApplicationBackupError):
            raise HTTPException(422, f'资料备份校验失败：{error}') from error
        if isinstance(error, OSError):
            raise HTTPException(500, f'资料备份读写失败：{error}') from error
        raise


@router.post('/restore')
def restore(file: UploadFile):
    restored = None
    try:
        # The staging copy is an internal verification step. Only committed live
        # records make this operation a restore from the user's perspective.
        with TemporaryDirectory(prefix='xwechat-data-restore-') as temporary:
            destination = Path(temporary) / 'verified'
            verified = restore_backup(file.file, destination)
            with account_work(verified['account']):
                restored = apply_backup(LibraryService(get_ai_service(), account_name), destination)
        return restored
    except ApplicationBackupConflict as error:
        raise HTTPException(409, f'资料恢复冲突，未写入：{error}') from error
    except ApplicationBackupError as error:
        raise HTTPException(422, f'资料恢复校验失败：{error}') from error
    except OSError as error:
        if restored is not None:
            raise HTTPException(500, {'code': 'restore_cleanup_failed', 'restored': restored,
                'message': f'资料已恢复，但临时校验文件清理失败：{error}'}) from error
        raise HTTPException(500, f'资料恢复读写失败：{error}') from error
