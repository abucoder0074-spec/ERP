from rest_framework.permissions import DjangoModelPermissions


class ModelPermissions(DjangoModelPermissions):
    perms_map = {
        **DjangoModelPermissions.perms_map,
        **dict.fromkeys(("GET", "HEAD", "OPTIONS"), ["%(app_label)s.view_%(model_name)s"]),
    }
