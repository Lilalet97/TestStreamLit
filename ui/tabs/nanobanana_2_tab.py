# ui/tabs/nanobanana_2_tab.py
"""NanoBanana 2 (Gemini 3.1 Flash Image) 페이지."""
from ui.tabs._nanobanana_factory import make_nanobanana_variant

TAB = make_nanobanana_variant(
    tab_id="nanobanana_2",
    title="\U0001f34c NanoBanana 2",
    feature_key="tab.nanobanana_2",
    get_model=lambda cfg: cfg.google_imagen_model_2,
    state_prefix="nb2",
    template_subdir="nanobanana_2",
    component_name="nanobanana_2_component",
    credit_feature="nanobanana_2",
)
