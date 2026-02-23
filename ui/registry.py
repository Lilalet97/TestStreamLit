# ui/registry.py
from dataclasses import dataclass
from typing import Callable, List, Set, Any, Dict

@dataclass(frozen=True)
class TabSpec:
    tab_id: str
    title: str
    required_features: Set[str]
    render: Callable[[Any, Any], None]  # (cfg, sidebar) 받는 render 함수로 변경

def get_all_tabs() -> List[TabSpec]:
    # 여기에서만 탭을 등록한다 (추가 시 이 파일만 수정)
    from ui.tabs.mj_tab import TAB as MJ_TAB
    from ui.tabs.gpt_tab import TAB as GPT_TAB
    from ui.tabs.suno_tab import TAB as SUNO_TAB
    from ui.tabs.kling_web_tab import TAB as KLING_WEB_TAB
    from ui.tabs.elevenlabs_tab import TAB as ELEVENLABS_TAB
    from ui.tabs.nanobanana_tab import TAB as NANOBANANA_TAB

    def _to_spec(d: Dict) -> TabSpec:
        return TabSpec(
            tab_id=d["tab_id"],
            title=d["title"],
            required_features=set(d.get("required_features") or set()),
            render=d["render"],
        )

    return [_to_spec(GPT_TAB), _to_spec(MJ_TAB), _to_spec(SUNO_TAB), _to_spec(KLING_WEB_TAB), _to_spec(ELEVENLABS_TAB), _to_spec(NANOBANANA_TAB)]


def filter_tabs(all_tabs: List[TabSpec], enabled_features: Set[str]) -> List[TabSpec]:
    """
    탭 required_features가 모두 enabled_features에 포함되면 노출.
    """
    out = []
    for t in all_tabs:
        if t.required_features.issubset(enabled_features):
            out.append(t)
    return out
