"""Единый формат ошибки API: {"code": ..., "message": ..., "details": {...}} (ТЗ-02 п. 6.1)."""
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.exceptions import APIException, ValidationError


class Conflict(APIException):
    """409: заявка уже обработана, дублирование и т. п. (ТЗ-02 п. 6.3)."""
    status_code = 409
    default_detail = 'Конфликт: объект уже обработан или дублируется.'
    default_code = 'conflict'


CODE_BY_STATUS = {
    400: 'validation_error',
    401: 'not_authenticated',
    403: 'forbidden',
    404: 'not_found',
    409: 'conflict',
    413: 'file_too_large',
    429: 'throttled',
    500: 'internal_error',
}


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    details = {}
    if isinstance(exc, ValidationError) and isinstance(response.data, dict):
        details = response.data
        message = 'Ошибка валидации.'
    elif isinstance(response.data, dict) and 'detail' in response.data:
        message = str(response.data['detail'])
    else:
        message = str(response.data)

    code = getattr(exc, 'default_code', None)
    if isinstance(getattr(exc, 'detail', None), (str,)) and getattr(exc.detail, 'code', None):
        code = exc.detail.code
    if not code or code in ('invalid', 'error'):
        code = CODE_BY_STATUS.get(response.status_code, 'error')

    response.data = {'code': code, 'message': message, 'details': details}
    return response
