from django.core.files.uploadedfile import UploadedFile
from django.core.files.uploadhandler import FileUploadHandler, StopUpload

MAX_IMPORT_BYTES = 2 * 1024 * 1024


class BoundedUploadHandler(FileUploadHandler):
    """Limit file bytes before Django's temporary-file handler writes them to disk."""

    received = 0

    def receive_data_chunk(self, raw_data: bytes, start: int) -> bytes:
        self.received += len(raw_data)
        if self.received > MAX_IMPORT_BYTES:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size: int) -> UploadedFile | None:
        return None
