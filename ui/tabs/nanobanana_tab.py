# ui/tabs/nanobanana_tab.py
"""NanoBanana 이미지 생성 페이지 — 팩토리 기반."""
from ui.tabs._nanobanana_factory import make_nanobanana_variant

TAB = make_nanobanana_variant(
    tab_id="nanobanana",
    title="\U0001f34c NanoBanana",
    feature_key="tab.nanobanana",
    get_model=lambda cfg: cfg.google_imagen_model,
    state_prefix="nb",
    template_subdir="nanobanana",
    component_name="nanobanana_component",
    credit_feature="nanobanana",
)
