from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """Пагинация по ТЗ-02 п. 6.1: page и page_size, по умолчанию 20, максимум 100."""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
