"""Тесты иконки приложения (ok_helper.appicon).

Иконка рисуется кодом — папка с надписью «OK». Тесты не требуют
экрана: conftest.py выставляет QT_QPA_PLATFORM=offscreen.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Иконка приложения
"""

import pytest

from ok_helper import appicon


pytestmark = [
    pytest.mark.gui,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Иконка приложения'),
]


@pytest.mark.story('Отрисовка иконки')
class TestAppIcon:
    @pytest.mark.smoke
    def test_icon_is_not_null_and_has_sizes(self, qapp):
        icon = appicon.app_icon()

        sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
        assert icon.isNull() is False
        assert (16, 16) in sizes
        assert (32, 32) in sizes
        assert (256, 256) in sizes

    def test_draw_image_has_requested_size(self, qapp):
        image = appicon.draw_icon_image(64)

        assert image.width() == 64
        assert image.height() == 64

    def test_draw_image_is_painted(self, qapp):
        image = appicon.draw_icon_image(48)

        painted = any(
            image.pixelColor(x, y).alpha() > 0
            for x in range(48) for y in range(48))
        assert painted, 'иконка не должна быть пустой'

    def test_png_bytes_is_png(self, qapp):
        data = appicon.png_bytes(32)

        assert data[:8] == b'\x89PNG\r\n\x1a\n'
        assert len(data) > 100
