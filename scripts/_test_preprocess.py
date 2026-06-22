"""快速测试 preprocess_prompt v3.3 集成。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agnes_common import preprocess_prompt, AgnesConfig

cfg = AgnesConfig(
    api_key="test", base_url="http://x", http_timeout=10,
    video_max_wait=10, video_poll_interval=2, output_dir=Path("."),
    translate_enabled=False,
)

cases = [
    ("T1 R1-JSON", '{"subject": "a cat", "style": "photorealistic"}'),
    ("T2 R2-woman", "A young woman sitting in a cafe"),
    ("T3 R3/R4-busty", "A busty young woman walking"),
    ("T4 anime→Japanese", "A 1girl anime school uniform"),
    ("T5 R2 旗袍", "A lady in qipao"),
    ("T6 R2 微乳", "一个微乳的美女"),
    ("T7 R1+aspect 摄影", '{"subject": "a cat", "aspect_ratio": "2:3"}'),
    ("T8 普通自然", "A landscape with mountains and rivers"),
]

for name, p in cases:
    r, m = preprocess_prompt(p, cfg)
    print(f"{name:30s} action={m['r_action']:35s} rules={m['r_rules']} nat={m['r_inferred_nationality']} ar={m['aspect_ratio']}")
    if p != r:
        print(f"  -> rewritten: {r[:100]}")
