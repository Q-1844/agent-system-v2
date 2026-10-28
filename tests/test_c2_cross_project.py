"""C2 frozen cross-project asset and append-only sequel/extension tests.

All tests are local/mocked: no paid media, network or GitHub operations.
"""
import asyncio
import hashlib
import json

import pytest

from src.ai.c2_production import (
    build_c2_blueprint, c2_duration_window, c2_media_profile,
    materialize_c2,
)
from src.ai.cross_project_assets import (
    AssetUnavailable, discover_project_assets, read_source_project,
    safe_project_dir, select_asset_uses, verify_asset_use,
)
from src.ai.producer_planning_models import TaskSpec, PlanningContractError
from src.ai.producer_planning_service import extract_duration_intent
from src.core.models import Project, Scene, Shot, VideoVersion, Character, StyleAnchor
from src.core.store import ProjectStore
from src.utils.project_manager import ProjectManager


LIMITS = {'text_calls': 12, 'image_calls': 10, 'video_seconds': 220}


class Ledger:
    def __init__(self):
        self.count = 0

    def reserve(self):
        self.count += 1


class FakeChat:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return json.dumps({
            'story_text': '新剧情：观测台上空一架飞机发现神秘的火光，继续前行。',
            'scenes': [
                {'title': '穿云', 'summary': '飞机穿越云层，发现远处的灯塔。', 'beats': 2},
                {'title': '灯塔', 'summary': '飞行员靠近灯塔，看见海浪。', 'beats': 3},
                {'title': '降落', 'summary': '飞机在晨光里安全降落。', 'beats': 2},
            ],
            'style': {'art_style': 'cinematic', 'palette': 'blue, amber', 'aspect_ratio': '16:9'},
        }, ensure_ascii=False)


def seed(root, name='origin', *, final_video=True):
    pm = ProjectManager(str(root))
    d = pm.create_project(name)
    clip = d / 'output' / 'videos' / 'source.mp4'
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b'unit-test-clip-bytes' * 12)
    ver = VideoVersion(version_id='original-v1', status='done', local=str(clip),
                       video_id='v-provider-original', seconds_returned=8)
    shot = Shot(shot_id='s01_c01', scene_id='s01', index=1,
                description='清晨灯塔上一架飞机飞过', target_duration_s=8,
                video_versions=[ver], final_video='original-v1' if final_video else None)
    project = Project(name=name, story_text='从灯塔起飞的一架飞机，向未知大陆寻找光。',
                      scenes=[Scene(scene_id='s01', title='起飞', summary='飞机从灯塔起飞', shots=[shot])],
                      characters=[Character(name='飞行员', description_en='a careful pilot')],
                      style=StyleAnchor(art_style='cinematic', palette='blue gold'),
                      next_scene_seq=2)
    ProjectStore(d).save(project)
    return d


def task(kind, text, source='origin'):
    return TaskSpec(intent=kind, request_text=text, source_project=source,
                    duration=extract_duration_intent(text))


def plan(root, kind, text):
    chat, ledger = FakeChat(), Ledger()
    blueprint = asyncio.run(build_c2_blueprint(task(kind, text),
        projects_root=root, client=chat, ledger=ledger, limits=LIMITS))
    assert chat.calls and ledger.count == len(chat.calls)
    return blueprint, chat


def test_cross_project_catalog_is_read_only_and_evidence_is_stable(tmp_path):
    root = tmp_path / 'projects'
    d = seed(root)
    before = (d / 'output' / 'project.json').read_bytes()
    candidates = discover_project_assets(root, '直接复用灯塔镜头', source_project='origin')
    video = next(c for c in candidates if c['kind'] == 'video')
    assert video['version_id'] == 'original-v1' and video['direct_reuse_eligible']
    assert video['sha256'] == hashlib.sha256((d/'output/videos/source.mp4').read_bytes()).hexdigest()
    selection = select_asset_uses(candidates, '直接复用灯塔镜头')
    assert selection[0]['action'] == 'reuse_media'
    assert verify_asset_use(root, selection[0]).is_file()
    assert (d / 'output' / 'project.json').read_bytes() == before


def test_catalog_rejects_traversal_symlink_and_mutated_sources(tmp_path):
    root = tmp_path / 'projects'
    d = seed(root)
    for value in ('../outside', '..', '/tmp', r'..\\evil'):
        with pytest.raises(ValueError):
            safe_project_dir(root, value)
    assets = discover_project_assets(root, '直接复用灯塔', source_project='origin')
    use = select_asset_uses(assets, '直接复用灯塔')[0]
    (d / 'output/videos/source.mp4').write_bytes(b'changed')
    with pytest.raises(AssetUnavailable, match='SHA256'):
        verify_asset_use(root, use)


def test_extension_differentiate_additional_and_total(tmp_path):
    root = tmp_path / 'projects'
    seed(root)
    add = task('extend_existing', '在原片后再加60秒新的画面')
    total = task('extend_existing', '把成片总长达到60秒')
    window_add, scope_add = c2_duration_window(add, 8, LIMITS)
    window_total, scope_total = c2_duration_window(total, 8, LIMITS)
    assert scope_add == 'additional_content' and scope_total == 'final_total'
    assert window_total[0] == window_add[0] - 8
    with pytest.raises(PlanningContractError):
        c2_duration_window(task('extend_existing', '把总长达到5秒'), 100, LIMITS)


def test_extend_append_only_reuses_clip_and_idempotent(tmp_path):
    root = tmp_path / 'projects'
    source = seed(root)
    seed(root, 'library')  # a distinct project's clip, not the already-used source
    original, source_sha = read_source_project(root, 'origin')
    bp, chat = plan(root, 'extend_existing', '直接复用灯塔原镜头，再加约100秒新的剧情')
    assert bp['schema_version'].startswith('producer-cross-project-c2')
    assert bp['scenes'][0]['scene_id'] == 's02'
    assert any(u['action'] == 'reuse_media' and u['evidence']['source_project'] == 'library'
               for u in bp['selected_assets'])
    assert all(u['action'] != 'reuse_media' for u in bp['selected_assets']
               if u['evidence']['source_project'] == 'origin')
    assert bp['source_shot_count'] == 1
    dest, info = materialize_c2(bp, projects_root=root, destination_name='origin',
                                plan_fingerprint='a' * 64)
    assert dest == source and info['imported']
    current = ProjectStore(dest).load()
    assert current.scenes[0].to_dict() == original.scenes[0].to_dict()
    assert len(current.scenes) == 4 and current.scenes[1].shots
    reused = current.scenes[1].shots[0]
    assert reused.final_video and reused.video_versions[0].generation_basis['cross_project_reuse']
    assert len(current.story_text) > len(original.story_text)
    m = (dest / 'output/project.json').read_bytes()
    dest2, info2 = materialize_c2(bp, projects_root=root, destination_name='origin',
                                  plan_fingerprint='a' * 64)
    assert not info2['imported'] and (dest2 / 'output/project.json').read_bytes() == m
    profile = c2_media_profile(bp)
    assert profile['scene_count'] == 4 and profile['shot_min'] >= 1
    assert profile['budget'] == LIMITS['video_seconds']


def test_sequel_creates_independent_project_and_leaves_source_unchanged(tmp_path):
    root = tmp_path / 'projects'
    src = seed(root)
    raw = (src / 'output/project.json').read_bytes()
    bp, _ = plan(root, 'sequel', '给灯塔飞机故事拍约120秒的续集')
    assert bp['scenes'][0]['scene_id'] == 's01'
    dest, _ = materialize_c2(bp, projects_root=root, destination_name='sequel-1')
    new = ProjectStore(dest).load()
    assert new.name == 'sequel-1' and len(new.scenes) == 3
    assert new.characters[0].name == '飞行员' and new.story_text == bp['story_text']
    assert (src / 'output/project.json').read_bytes() == raw
    dest2, _ = materialize_c2(bp, projects_root=root, destination_name='sequel-1')
    assert dest2 == dest and len(ProjectStore(dest2).load().scenes) == 3


def test_reused_clip_shot_video_asset_mirrors_final_version(tmp_path):
    """复用镜头的 Shot.video 必须与 final_video 版本一致。

    回归 LOCKED_SHOT_NO_FINAL：materialize_c2 原先只写 final_video +
    video_versions，Shot.video 停在 pending。锁定校验与 upstream_ready 读的
    都是 Shot.video，于是已完成的复用镜头被当成「没有成片」。
    """
    from src.ai.autopilot import _locked_shot_problems

    class _Planned:
        def __init__(self, shot_id, requires_previous_video_end=False):
            self.shot_id = shot_id
            self.requires_previous_video_end = requires_previous_video_end

    class _Plan:
        def __init__(self, shots):
            self.shots = shots

    root = tmp_path / 'projects'
    seed(root)
    bp, _ = plan(root, 'sequel', '必须直接复用旧片镜头制作大约一分钟的续集')
    reuse = [u for u in bp['selected_assets'] if u['action'] == 'reuse_media']
    assert reuse, 'this test needs a reuse_media selection'
    dest, _info = materialize_c2(bp, projects_root=root,
                                 destination_name='sequel-reuse',
                                 plan_fingerprint='d' * 64)
    project = ProjectStore(dest).load()
    reused = project.scenes[0].shots[0]
    version = next(v for v in reused.video_versions
                   if v.version_id == reused.final_video)
    assert reused.video.status == 'done'
    assert reused.video.local == version.local
    assert reused.video.video_id == version.video_id
    assert reused.video.seconds_returned == version.seconds_returned
    # 锁定校验读 Shot.video：完成态的复用镜头不得再被判成没有成片。
    reused.locked = True
    ProjectStore(dest).save(project)
    problems = _locked_shot_problems(
        project.scenes[0],
        _Plan([_Planned('s01_c01')]),
        True)
    assert problems == []


def test_extension_rejects_external_source_story_edit(tmp_path):
    root=tmp_path/'projects'
    d=seed(root)
    bp,_=plan(root,'extend_existing','为灯塔旧片追加大约100秒剧情')
    project=ProjectStore(d).load()
    project.story_text='external overwrite'
    ProjectStore(d).save(project)
    with pytest.raises(AssetUnavailable):
        materialize_c2(bp,projects_root=root,destination_name='origin')


def test_c2_reads_existing_scene_system_and_resource_continuity_without_mutation(tmp_path):
    from src.scene_system.store import SceneSystemStore
    from src.scene_system.models import (Location, SpatialAnchor, SceneInstance, SceneState)
    from src.resources.store import ResourceStore
    from src.resources.models import Prop
    root = tmp_path / 'projects'
    d = seed(root)
    scene_store = SceneSystemStore(d)
    doc = scene_store.load()
    doc.locations['loc-1'] = Location(location_id='loc-1', name='灯塔控制室',
                                      identity_description='西侧一排蓝色窗户')
    doc.anchors['anc-1'] = SpatialAnchor(anchor_id='anc-1', location_id='loc-1',
                                         name='塔顶钟', semantic_zone='north_wall')
    doc.scene_instances['inst-1'] = SceneInstance(
        scene_instance_id='inst-1', story_scene_id='s01', location_id='loc-1',
        state=SceneState(time_of_day='dawn', weather='clear', lighting='amber'))
    scene_store.save(doc)
    resource_store = ResourceStore(d)
    rdoc = resource_store.load()
    rdoc.props['p01'] = Prop(prop_id='p01', name='红色望远镜', description='放在塔楼窗边')
    resource_store.save(rdoc)
    before = [(p, p.read_bytes()) for p in (scene_store.path, resource_store.path)]
    blueprint, client = plan(root, 'sequel', '继续灯塔故事，续集约120秒')
    ctx = blueprint['source_continuity_context']
    assert ctx['locations'][0]['name'] == '灯塔控制室'
    assert ctx['spatial_anchors'][0]['name'] == '塔顶钟'
    assert ctx['recent_scene_states'][0]['time_of_day'] == 'dawn'
    assert ctx['resource_props'][0]['name'] == '红色望远镜'
    assert all(p.read_bytes() == old for p, old in before)
    destination, _ = materialize_c2(blueprint, projects_root=root,
                                    destination_name='nextsequel',
                                    plan_fingerprint='c' * 64)
    from src.ai.full_project_autopilot import c2_scene_reference_note
    note = c2_scene_reference_note(destination, 's01')
    assert '灯塔控制室' in note and 'dawn' in note
    assert c2_scene_reference_note(destination, 'not-in-sequel') == ''


def test_resource_reference_asset_from_other_project_is_readonly_and_verified(tmp_path):
    from src.resources.store import ResourceStore
    from src.resources.models import ReferenceAsset
    root = tmp_path / 'projects'
    source = seed(root, 'archivedsource')
    image = source / 'output' / 'resources' / 'hero.png'
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b'original-looking-reference-image')
    fingerprint = hashlib.sha256(image.read_bytes()).hexdigest()
    store = ResourceStore(source)
    doc = store.load()
    doc.reference_assets['ref_icon'] = ReferenceAsset(
        asset_id='ref_icon', purpose='灯塔风格', view='hero',
        local=str(image), checksum=fingerprint)
    store.save(doc)
    before = store.path.read_bytes()
    catalog = discover_project_assets(root, '参考灯塔风格', source_project='archivedsource')
    reference = next(c for c in catalog if c['source_kind'] == 'resource')
    assert reference['kind'] == 'image' and reference['version_id'] == 'ref_icon'
    use = select_asset_uses([reference], '直接复用灯塔风格')[0]
    assert use['action'] == 'reference_only'
    assert verify_asset_use(root, use) == image
    assert store.path.read_bytes() == before
    image.write_bytes(b'altered-reference')
    with pytest.raises(AssetUnavailable, match='SHA256'):
        verify_asset_use(root, use)


def test_extension_incomplete_source_requires_c1_completion_first(tmp_path):
    root = tmp_path / 'projects'
    seed(root, final_video=False)
    with pytest.raises(PlanningContractError, match='complete_project'):
        plan(root, 'extend_existing', '把旧片延长大概60秒')
