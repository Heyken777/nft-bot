from django.http import HttpResponse


def custom_404(request, exception):
    return HttpResponse('Ошибка. Вернитесь на главную: http://93.115.101', status=404)


def custom_500(request):
    return HttpResponse('Ошибка. Вернитесь на главную: http://93.115.101', status=500)
