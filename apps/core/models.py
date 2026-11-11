from django.db import models


class Workspace(models.Model):
    """Content type for permissions that apply to the whole workspace."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [("access_workspace", "Can access ERP workspace")]
