# ui/tabs/nanobanana_pro_tab.py
"""NanoBanana Pro (Gemini 3 Pro Image) 페이지."""
from ui.tabs._nanobanana_factory import make_nanobanana_variant

TAB = make_nanobanana_variant(
    tab_id="nanobanana_pro",
    title="NanoBanana Pro",
    feature_key="tab.nanobanana_pro",
    get_model=lambda cfg: cfg.google_imagen_model_pro,
    state_prefix="nbp",
    template_subdir="nanobanana_pro",
    component_name="nanobanana_pro_component",
    credit_feature="nanobanana_pro",
)
