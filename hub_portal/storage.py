from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class HubStaticFilesStorage(ManifestStaticFilesStorage):
    manifest_strict = False
