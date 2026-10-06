# Phase48 B批：原生音效保留与装配政策回归（templates 契约 + assembly 角色政策）。
# 零 LLM/派发：只断言政策文本与模板默认值（真实生产行为由 W08 真机 run 验证，见报告）。
import src.workflow.templates as T
from src.agents.assembly import ASSEMBLY_SYSTEM


def _w08_def():
    return T.create_from_template("W08", "wf_test_w08_b", {})


def _w09_def():
    return T.create_from_template("W09", "wf_test_w09_b", {})


def _asm_node(defn, node_id="n_asm"):
    return next(n for n in defn["nodes"] if n["id"] == node_id)


def test_w08_w09_default_objectives_native_audio_policy():
    """默认装配目标必须承载 Phase48 音频政策：无自动旁白 + 默认保留原生音效。"""
    for defn in (_w08_def(), _w09_def()):
        obj = _asm_node(defn)["config"]["objective"]
        assert "禁止自动旁白" in obj
        assert "保留合格原生" in obj
        assert "逐镜" in obj  # 先分类再决定
        assert "音乐仅按作品政策" in obj


def test_w09_default_no_longer_mutes_native_audio():
    """Phase47 默认目标「外部旁白+连续音乐，原视频静音/控音」必须被替换——
    缺音乐不等于该静音（用户观察 + Phase48 总控 §1）。"""
    obj = _asm_node(_w09_def())["config"]["objective"]
    assert "原视频静音/控音" not in obj
    assert "外部旁白" not in obj


def test_template_version_bumped():
    """政策为实质变更：模板版本必须升级，新定义可追溯到 p48。"""
    assert T.TEMPLATE_VERSION == "p48_e_templates_v1"
    assert _w08_def()["template"]["templateVersion"] == "p48_e_templates_v1"


def test_w08_metadata_audio_policy():
    meta = T.TEMPLATES["W08"]
    assert any("音频政策" in x for x in meta["requiredInputs"])
    assert not any("旁白和音乐政策" in x for x in meta["requiredInputs"])
    assert any("原生保留或按政策" in x for x in meta["expectedOutputs"])


def test_assembly_system_policy_lines():
    """装配角色政策核心条款：默认保留 / 禁自动旁白 / 无配乐≠静音 / 先分类后决定。"""
    assert "默认保留合格原生环境声与动作声" in ASSEMBLY_SYSTEM
    assert "全片禁止自动旁白" in ASSEMBLY_SYSTEM
    assert '"没有配乐"≠"应该静音"' in ASSEMBLY_SYSTEM
    assert "先分类后决定" in ASSEMBLY_SYSTEM
    assert "【合格环境声/动作声】" in ASSEMBLY_SYSTEM
    # 旁白能力保留但降级为 brief 显式要求
    assert "仅当用户 brief 明确要求旁白时才走 narration_read" in ASSEMBLY_SYSTEM
    # 整条静音只允许显式策略
    assert "audio=silent 或用户 brief 明确要求" in ASSEMBLY_SYSTEM
    # 原音静默显式策略语义保留
    assert "plan_config.base_gain_db=-60" in ASSEMBLY_SYSTEM
