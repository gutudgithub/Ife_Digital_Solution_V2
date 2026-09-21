from django.contrib.auth.views import LogoutView
from django.http import HttpRequest, HttpResponse


class SecureLogoutView(LogoutView):
    def post(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        response = super().post(request, *args, **kwargs)
        response.headers["Clear-Site-Data"] = '"cache", "storage"'
        return response
