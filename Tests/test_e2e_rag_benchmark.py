# -*- coding: utf-8 -*-
"""
AxonBot: E2E Production RAG Benchmark Suite
Executes end-to-end evaluation across 61 production cases:
- 45 stress cases (Data/test_bench_kr_dataset.json)
- 16 cross-lingual benchmarks (Data/test_cross_lingual_dataset.json)

Usage:
  pytest Tests/test_e2e_rag_benchmark.py --suite=production
  pytest Tests/test_e2e_rag_benchmark.py --suite=smoke
  python Tests/test_e2e_rag_benchmark.py --suite=production
"""

import os
import sys
import re
import time
import json
import uuid
import asyncio
import aiohttp
import pytest
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Scripts.bot.config import N8N_RAG_URL
from Scripts.bot.utils.query_preprocessor import preprocess_query
from Tests.rag_evaluator import RAGEvaluator, JudgeVerdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Attachment Integrity Cache
MEDIA_CACHE_PATH = PROJECT_ROOT / "Data" / "media_captions_cache.json"
VALID_MEDIA_FILENAMES: set[str] = set()
if MEDIA_CACHE_PATH.exists():
    try:
        with open(MEDIA_CACHE_PATH, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
            VALID_MEDIA_FILENAMES = {k.lower() for k in cache_data.get("by_filename", {}).keys()}
    except Exception as e:
        pass

EVALUATOR = RAGEvaluator(model="gemini-3.5-flash-lite")
DATASET_STRESS_PATH = PROJECT_ROOT / "Data" / "test_bench_kr_dataset.json"
DATASET_CROSS_LINGUAL_PATH = PROJECT_ROOT / "Data" / "test_cross_lingual_dataset.json"

# Deterministic slug mappings matching presentation benchmarks
SLUG_MAPPINGS: Dict[str, str] = {
    # 45 Stress Cases
    "ST-01": "test_excavation_dwg_mesh_modeling_workflow",
    "ST-02": "test_excavation_workset_and_category_assignment",
    "ST-03": "test_pile_field_cutoff_parameter_and_plugin",
    "ST-04": "test_formwork_wall_pylon_joining_hierarchy",
    "ST-05": "test_formwork_acute_angle_wall_miter_niche",
    "ST-06": "test_beam_slab_join_geometry_priority",
    "ST-07": "test_foundation_pit_sloped_family_usage",
    "ST-08": "test_confluence_visual_schematic_grounding",
    "ST-09": "test_rebar_anchorage_and_lap_formulas",
    "ST-10": "test_rebar_solid_view_3d_visibility",
    "ST-11": "test_rebar_background_mesh_parameters",
    "ST-12": "test_rebar_running_meter_schedule_parameters",
    "ST-13": "test_rebar_dowels_2d_plan_annotation",
    "ST-14": "test_wall_edge_reinforcement_pins_and_ties",
    "ST-15": "test_wall_corner_host_thickness_reinforcement",
    "ST-16": "test_rebar_couplers_and_specialized_families",
    "ST-17": "test_balcony_slab_monolithic_thermal_break_join",
    "ST-18": "test_balcony_thermal_insert_family_parameters",
    "ST-19": "test_stair_landing_support_cantilever_toggle",
    "ST-20": "test_stair_flight_riser_calculation_rules",
    "ST-21": "test_stair_header_beam_rebate_cutout",
    "ST-22": "test_stairs_view_template_and_filters",
    "ST-23": "test_naming_convention_gk_vk_abbreviations",
    "ST-24": "test_naming_mask_system_families_gk",
    "ST-25": "test_naming_mask_system_families_vk",
    "ST-26": "test_naming_mask_in_place_component",
    "ST-27": "test_naming_mask_groups_and_assemblies",
    "ST-28": "test_naming_mask_formwork_floor_plan",
    "ST-29": "test_views_typical_floor_section_visibility",
    "ST-30": "test_schedules_precast_concrete_filter_rules",
    "ST-31": "test_openings_automated_shaft_penetration",
    "ST-32": "test_material_concrete_grade_specification",
    "ST-33": "test_wall_profile_direct_editing_prohibition",
    "ST-34": "test_in_place_ramp_modeling_prohibition",
    "ST-35": "test_groups_rebar_host_selection_and_ungroup",
    "ST-36": "test_monolithic_slab_wall_cut_hierarchy",
    "ST-37": "test_out_of_scope_architectural_facade_refusal",
    "ST-38": "test_out_of_scope_plumbing_drainage_refusal",
    "ST-39": "test_out_of_scope_electrical_cable_tray_refusal",
    "ST-40": "test_out_of_scope_structural_software_refusal",
    "ST-41": "test_kr_parameters_drawing_set_general_notes",
    "ST-42": "test_kr_parameters_concrete_consumption_schedule",
    "ST-43": "test_kr_parameters_opening_base_elevation_datum",
    "ST-44": "test_kr_parameters_opening_discipline_source",
    "ST-45": "test_kr_parameters_precast_catalog_series_code",

    # 16 Cross-Lingual Benchmarks
    "CL-01-RU": "test_cross_lingual_expansion_joint_slab_ru",
    "CL-01-EN": "test_cross_lingual_ru_parameters_preservation",
    "CL-02-RU": "test_cross_lingual_stair_header_beam_ru",
    "CL-02-EN": "test_cross_lingual_stair_header_beam_en",
    "CL-03-RU": "test_cross_lingual_demolished_structures_phasing_ru",
    "CL-03-EN": "test_cross_lingual_demolished_structures_phasing_en",
    "CL-04-RU": "test_cross_lingual_rebar_cover_area_reinforcement_ru",
    "CL-04-EN": "test_cross_lingual_rebar_cover_area_reinforcement_en",
    "CL-05-RU": "test_cross_lingual_shaft_openings_slabs_ru",
    "CL-05-EN": "test_cross_lingual_shaft_openings_slabs_en",
    "CL-06-RU": "test_cross_lingual_material_consumption_schedule_ru",
    "CL-06-EN": "test_cross_lingual_material_consumption_schedule_en",
    "CL-07-RU": "test_cross_lingual_scad_office_load_export_ru_refusal",
    "CL-07-EN": "test_cross_lingual_scad_office_load_export_en_refusal",
    "CL-08-RU": "test_cross_lingual_post_tensioned_tendons_ru_refusal",
    "CL-08-EN": "test_cross_lingual_post_tensioned_tendons_en_refusal",
}

@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    suite: str  # "stress" or "cross_lingual"
    name: str
    slug: str
    question: str
    lang: str = "ru"
    expected_keywords_any: list[list[str]] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    expected_media: bool = False
    allowed_page_ids: list[int] = field(default_factory=list)
    expected_mask_regex: Optional[str] = None
    expected_source_pattern: Optional[str] = None
    expected_rubric: Optional[str] = None
    is_refusal_test: bool = False
    max_latency_sec: float = 35.0

@dataclass
class BenchmarkResult:
    case_id: str
    slug: str
    suite: str
    name: str
    question: str
    passed: bool
    status_code: int
    latency_sec: float
    retrieval_score: float
    factual_score: float
    media_score: float
    rejection_score: float
    composite_score: float
    grade: str
    response_text: str
    found_page_ids: list[int] = field(default_factory=list)
    found_media: list[str] = field(default_factory=list)
    hallucinated_media: list[str] = field(default_factory=list)
    breakdown_notes: list[str] = field(default_factory=list)
    error_message: Optional[str] = None
    judge_verdict: Optional[JudgeVerdict] = None

# Global execution storage for terminal summary (shared via pytest namespace)
if not hasattr(pytest, "axon_session_results"):
    pytest.axon_session_results = []
SESSION_RESULTS = pytest.axon_session_results

def load_all_cases() -> List[BenchmarkCase]:
    cases: List[BenchmarkCase] = []

    # 1. Load 45 Stress Cases
    if DATASET_STRESS_PATH.exists():
        with open(DATASET_STRESS_PATH, "r", encoding="utf-8") as f:
            for item in json.load(f):
                cid = item["id"]
                slug = SLUG_MAPPINGS.get(cid, f"test_{cid.lower().replace('-', '_')}")
                cases.append(
                    BenchmarkCase(
                        id=cid,
                        suite="stress",
                        name=item["name"],
                        slug=slug,
                        question=item["question"],
                        lang="ru",
                        expected_keywords_any=item.get("expected_keywords_any", []),
                        forbidden_keywords=item.get("forbidden_keywords", []),
                        expected_media=item.get("expected_media", False),
                        allowed_page_ids=item.get("allowed_page_ids", []),
                        expected_mask_regex=item.get("expected_mask_regex"),
                        expected_source_pattern=item.get("expected_source_pattern"),
                        expected_rubric=item.get("expected_rubric"),
                        is_refusal_test=item.get("is_refusal_test", False),
                        max_latency_sec=item.get("max_latency_sec", 35.0),
                    )
                )

    # 2. Load 16 Cross-Lingual Cases
    if DATASET_CROSS_LINGUAL_PATH.exists():
        with open(DATASET_CROSS_LINGUAL_PATH, "r", encoding="utf-8") as f:
            for item in json.load(f):
                cid = item["id"]
                slug = SLUG_MAPPINGS.get(cid, f"test_{cid.lower().replace('-', '_')}")
                cases.append(
                    BenchmarkCase(
                        id=cid,
                        suite="cross_lingual",
                        name=item["name"],
                        slug=slug,
                        question=item["question"],
                        lang=item.get("lang", "en"),
                        expected_keywords_any=item.get("expected_keywords_any", []),
                        forbidden_keywords=[],
                        expected_media=item.get("expected_media", False),
                        allowed_page_ids=item.get("allowed_page_ids", []),
                        expected_mask_regex=None,
                        expected_source_pattern=None,
                        expected_rubric=item.get("expected_rubric"),
                        is_refusal_test=item.get("is_refusal_test", False),
                        max_latency_sec=35.0,
                    )
                )

    return cases

ALL_CASES = load_all_cases()
CASES_BY_SLUG = {c.slug: c for c in ALL_CASES}

async def execute_benchmark_case(case: BenchmarkCase) -> BenchmarkResult:
    session_uid = f"bench_{uuid.uuid4().hex[:12]}"
    normalized_q = preprocess_query(case.question) if case.lang == "ru" else case.question
    payload = {
        "text": normalized_q,
        "sessionId": session_uid,
        "chatId": session_uid,
        "chat_id": session_uid,
    }

    t0 = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                N8N_RAG_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=case.max_latency_sec),
            ) as resp:
                latency = round(time.perf_counter() - t0, 2)
                status_code = resp.status
                if status_code != 200:
                    raw_text = await resp.text()
                    res = BenchmarkResult(
                        case_id=case.id,
                        slug=case.slug,
                        suite=case.suite,
                        name=case.name,
                        question=case.question,
                        passed=False,
                        status_code=status_code,
                        latency_sec=latency,
                        retrieval_score=0.0,
                        factual_score=0.0,
                        media_score=0.0,
                        rejection_score=0.0,
                        composite_score=0.0,
                        grade="🔴 FAIL",
                        response_text=raw_text,
                        error_message=f"HTTP {status_code}",
                    )
                    SESSION_RESULTS.append(res)
                    return res

                data = await resp.json()
                raw_output = data.get("output", "")
                if isinstance(raw_output, dict):
                    response_text = raw_output.get("text", str(raw_output))
                else:
                    response_text = str(raw_output)
        except Exception as e:
            latency = round(time.perf_counter() - t0, 2)
            res = BenchmarkResult(
                case_id=case.id,
                slug=case.slug,
                suite=case.suite,
                name=case.name,
                question=case.question,
                passed=False,
                status_code=500,
                latency_sec=latency,
                retrieval_score=0.0,
                factual_score=0.0,
                media_score=0.0,
                rejection_score=0.0,
                composite_score=0.0,
                grade="🔴 FAIL",
                response_text="",
                error_message=str(e),
            )
            SESSION_RESULTS.append(res)
            return res

    # 1. Media Parsing
    found_media = re.findall(r"(?:!\[[^\]]*\]|\[(?:Изображение|Image|Анимация|Animation):[^\]]*\])\((https?://[^\)]+)\)", response_text, re.IGNORECASE)
    tag_filenames = re.findall(r"\[(?:Изображение|Image|Анимация|Animation):\s*([a-zA-Z0-9_\-\.]+\.(?:png|jpg|jpeg|gif))", response_text, re.IGNORECASE)
    url_filenames = [u.split("/")[-1].split("?")[0] for u in found_media]
    all_referenced_media = set(f.strip() for f in (tag_filenames + url_filenames) if f.strip())

    hallucinated_media = [
        f for f in all_referenced_media
        if VALID_MEDIA_FILENAMES and f.lower() not in VALID_MEDIA_FILENAMES
    ]

    # 2. Confluence Page IDs
    found_page_ids = [int(pid) for pid in re.findall(r"pages/(\d+)", response_text)]
    if not found_page_ids:
        found_page_ids = [int(pid) for pid in re.findall(r"attachments/(\d+)", response_text)]

    resp_lower = response_text.lower()
    breakdown_notes = []

    # 3. Refusal Evaluation (Strict Refusal Grounding)
    if case.is_refusal_test:
        refusal_markers = [
            "нет информации",
            "не относится к",
            "вне компетенции",
            "обратитесь к bim-координатору",
            "не содержит",
            "только по разделу кр",
            "не найдено",
            "не входит в регламент",
            "no guidelines",
            "consult with the bim",
            "refer to the bim",
            "bim department",
            "out of scope",
        ]
        has_refusal = any(m in resp_lower for m in refusal_markers)
        has_source = "📌" in response_text or "atlassian.net" in response_text or len(found_page_ids) > 0
        has_media = len(found_media) > 0 or len(all_referenced_media) > 0

        rejection_score = 100.0 if has_refusal else 0.0
        retrieval_score = 100.0 if not has_source else 0.0
        media_score = 100.0 if (not has_media and len(hallucinated_media) == 0) else 0.0
        factual_score = 100.0 if has_refusal else 0.0

        if not has_refusal:
            breakdown_notes.append("Refusal missing on Out-of-Scope query")
        if has_source:
            breakdown_notes.append(f"Spurious source attribution upon refusal (IDs: {found_page_ids})")
        if has_media:
            breakdown_notes.append(f"Spurious media upon refusal: {list(all_referenced_media)}")

        composite_score = round(0.50 * rejection_score + 0.30 * retrieval_score + 0.20 * media_score, 1)
        passed = composite_score >= 80.0
        grade = "🟢 EXCELLENT" if composite_score >= 90 else "🟡 GOOD" if composite_score >= 70 else "🔴 FAIL"

        res = BenchmarkResult(
            case_id=case.id,
            slug=case.slug,
            suite=case.suite,
            name=case.name,
            question=case.question,
            passed=passed,
            status_code=status_code,
            latency_sec=latency,
            retrieval_score=retrieval_score,
            factual_score=factual_score,
            media_score=media_score,
            rejection_score=rejection_score,
            composite_score=composite_score,
            grade=grade,
            response_text=response_text,
            found_page_ids=found_page_ids,
            found_media=list(all_referenced_media),
            hallucinated_media=hallucinated_media,
            breakdown_notes=breakdown_notes,
            error_message="; ".join(breakdown_notes) if not passed else None,
        )
        SESSION_RESULTS.append(res)
        return res

    # 4. Standard / Cross-Lingual Knowledge Case Evaluation
    # 4.1. Keyword matching
    matched_groups = 0
    for grp in case.expected_keywords_any:
        if any(kw.lower() in resp_lower for kw in grp):
            matched_groups += 1

    # 4.2. Retrieval & Source Scoring
    source_found = True
    if case.expected_source_pattern:
        source_found = bool(re.search(case.expected_source_pattern, response_text, re.IGNORECASE))

    page_id_valid = True
    if case.allowed_page_ids:
        page_id_valid = bool(found_page_ids) and any(pid in case.allowed_page_ids for pid in found_page_ids)

    if page_id_valid and source_found:
        retrieval_score = 100.0
    elif page_id_valid and not source_found:
        retrieval_score = 70.0
    elif not page_id_valid and found_page_ids:
        retrieval_score = 40.0
        breakdown_notes.append(f"Foreign Page ID: {found_page_ids}")
    else:
        retrieval_score = 0.0
        breakdown_notes.append("Missing Confluence link")

    # 4.3. Media Scoring
    if hallucinated_media:
        media_score = 0.0
        breakdown_notes.append(f"Hallucinated media: {hallucinated_media}")
    elif case.expected_media:
        media_score = 100.0 if len(all_referenced_media) > 0 else 0.0
        if media_score == 0.0:
            breakdown_notes.append("Expected media not returned")
    else:
        media_score = 100.0

    # 4.4. Mask Regex Check
    mask_matched = True
    if case.expected_mask_regex:
        mask_matched = bool(re.search(case.expected_mask_regex, response_text, re.IGNORECASE))
        if not mask_matched:
            breakdown_notes.append(f"Mask regex mismatch: {case.expected_mask_regex}")

    # 4.5. Factual / Engineering Scoring (LLM-as-a-Judge)
    judge_verdict = None
    if EVALUATOR.is_available and case.expected_rubric:
        judge_verdict = await EVALUATOR.evaluate(
            question=case.question,
            response_text=response_text,
            expected_rubric=case.expected_rubric,
            is_refusal_test=case.is_refusal_test,
        )
        if judge_verdict.is_correct is True:
            factual_score = 100.0 if judge_verdict.confidence >= 0.8 else 85.0
        elif judge_verdict.is_correct is False:
            if "нет информации" in resp_lower or "обратитесь к bim-координатору" in resp_lower:
                factual_score = 0.0
                breakdown_notes.append(f"False refusal: {judge_verdict.verdict_reason}")
            else:
                factual_score = 45.0
                breakdown_notes.append(f"Engineering critique: {judge_verdict.verdict_reason}")
        else:
            kw_ratio = matched_groups / max(1, len(case.expected_keywords_any))
            factual_score = round(kw_ratio * 100, 1)
    else:
        kw_ratio = matched_groups / max(1, len(case.expected_keywords_any))
        factual_score = round(kw_ratio * 100, 1)

    if not mask_matched:
        factual_score = min(factual_score, 50.0)

    # 4.6. Composite Score
    latency_score = 100.0 if latency <= 10.0 else max(0.0, 100.0 - (latency - 10.0) * 5)
    composite_score = round(
        0.45 * factual_score + 0.35 * retrieval_score + 0.15 * media_score + 0.05 * latency_score,
        1
    )

    passed = (composite_score >= 70.0) and (retrieval_score >= 40.0) and (factual_score >= 40.0)
    grade = (
        "🟢 EXCELLENT" if composite_score >= 90.0
        else "🟡 GOOD" if composite_score >= 70.0
        else "🔴 FAIL"
    )

    res = BenchmarkResult(
        case_id=case.id,
        slug=case.slug,
        suite=case.suite,
        name=case.name,
        question=case.question,
        passed=passed,
        status_code=status_code,
        latency_sec=latency,
        retrieval_score=retrieval_score,
        factual_score=factual_score,
        media_score=media_score,
        rejection_score=100.0,
        composite_score=composite_score,
        grade=grade,
        response_text=response_text,
        found_page_ids=found_page_ids,
        found_media=list(all_referenced_media),
        hallucinated_media=hallucinated_media,
        breakdown_notes=breakdown_notes,
        error_message="; ".join(breakdown_notes) if not passed else None,
        judge_verdict=judge_verdict,
    )
    SESSION_RESULTS.append(res)
    return res

# Dynamically generate named test functions in module scope for clean pytest item discovery
for _case in ALL_CASES:
    def _create_test(c: BenchmarkCase):
        @pytest.mark.asyncio
        async def _test_body():
            result = await execute_benchmark_case(c)
            assert result.passed, (
                f"[{result.case_id}] {result.name} {result.grade} (Score: {result.composite_score}%)\n"
                f"Retrieval: {result.retrieval_score}%, Factual: {result.factual_score}%, Media: {result.media_score}%\n"
                f"Notes: {result.breakdown_notes}\n"
                f"Response: {result.response_text[:300]}..."
            )
        _test_body.__name__ = c.slug
        _test_body.__qualname__ = c.slug
        _test_body.__doc__ = f"[{c.id}] {c.name}"
        _test_body.case = c
        return _test_body

    globals()[_case.slug] = _create_test(_case)

if __name__ == "__main__":
    suite_arg = "--suite=production"
    for arg in sys.argv[1:]:
        if arg.startswith("--suite="):
            suite_arg = arg
            break
    sys.exit(pytest.main([__file__, suite_arg, "-s"]))
