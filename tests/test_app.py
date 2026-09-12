from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_app_loads():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=10).run()
    assert not app.exception
    assert app.title[0].value == "商机录入与分析 Agent"
    assert any(button.label == "开始分析" for button in app.button)
    assert any(button.label == "重置" for button in app.button)
    assert app.text_area[0].label == "销售拜访记录"
