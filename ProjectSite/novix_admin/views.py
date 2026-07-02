from django.http import HttpResponse


def custom_404(request, exception):
    return HttpResponse('Ошибка. Вернитесь на главную: http://127.0.0.1:8000/usersite/', status=404)


def custom_500(request):
    return HttpResponse('Ошибка. Вернитесь на главную: http://127.0.0.1:8000/usersite/', status=500)
