"""Tests for the flow engine (Task 2): registry, build_flow, execute_flow."""

import copy
import unittest
from unittest import mock

from api.flow import REGISTRY, _register_defaults, _unimplemented_handler
from api import (
    CrawlContext,
    CrawlRequest,
    InvalidFlowError,
    MissingFlowError,
    build_flow,
    execute_flow,
    get_module,
    is_registered,
    list_modules,
    register_module,
    set_module_handler,
)
from api.contracts import ContentType, OutputFormat


def _req(**overrides):
    base = dict(
        url="https://example.com/story",
        content_type=ContentType.NOVEL,
        output_format=OutputFormat.EPUB,
    )
    base.update(overrides)
    return CrawlRequest(**base)


VALID_FLOW = [
    {"module": "get_metadata", "params": {"expected_selector": "h1.entry-title"}},
    {"module": "volumes_prepare", "params": {"expected_selector": "ul.chapters"}},
    {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content", "download_image"]}},
    {"module": "create_ebook"},
]


def _fmt(flow=None):
    return {"content_type": "novel", "flow": flow}


def step_names(flow):
    return [step.module for step in flow.steps]


def active_names(flow):
    return [step.module for step in flow.active_steps]


class TestRegistry(unittest.TestCase):
    def test_canonical_modules_registered(self):
        for name in (
            "get_user_input",
            "get_metadata",
            "volumes_prepare",
            "crawl_chapter",
            "create_ebook",
        ):
            self.assertTrue(is_registered(name), name)

    def test_registered_names_stable(self):
        self.assertEqual(
            list_modules(),
            (
                "crawl_chapter",
                "create_ebook",
                "get_metadata",
                "get_user_input",
                "volumes_prepare",
            ),
        )

    def test_register_rejects_non_callable_handler(self):
        with self.assertRaises(ValueError):
            register_module("bogus", handler="not-callable")

    def test_set_module_handler_preserves_structure(self):
        before = get_module("get_metadata")
        set_module_handler("get_metadata", lambda ctx, params: None)
        after = get_module("get_metadata")
        self.assertEqual(after.stage, "metadata")
        self.assertEqual(after.required, before.required)
        self.assertEqual(after.allowed_params, before.allowed_params)
        _register_defaults()

    def test_canonical_stage_is_immutable(self):
        with self.assertRaises(ValueError):
            register_module(
                "get_metadata",
                handler=_unimplemented_handler("get_metadata"),
                stage="export",
            )

    def test_canonical_required_and_occurrence_limits_are_immutable(self):
        with self.assertRaises(ValueError):
            register_module(
                "get_metadata",
                handler=_unimplemented_handler("get_metadata"),
                required=False,
            )
        with self.assertRaises(ValueError):
            register_module(
                "get_metadata",
                handler=_unimplemented_handler("get_metadata"),
                max_occurrences=2,
            )

    def test_custom_module_can_be_registered(self):
        register_module(
            "my_export",
            handler=lambda ctx, params: None,
            stage="export",
            allowed_params=frozenset({"payload"}),
        )
        self.assertTrue(is_registered("my_export"))
        REGISTRY.pop("my_export", None)


class TestBuildFlowValidation(unittest.TestCase):
    def test_valid_stored_flow(self):
        flow = build_flow(_req(), _fmt(VALID_FLOW))
        self.assertEqual(
            step_names(flow),
            ["get_metadata", "volumes_prepare", "crawl_chapter", "create_ebook"],
        )
        self.assertEqual(active_names(flow), step_names(flow))

    def test_valid_flow_from_request_dict(self):
        flow = build_flow(
            {"url": "example.com/s", "content_type": "novel", "output_format": "epub"},
            _fmt(VALID_FLOW),
        )
        self.assertIsInstance(flow.request, CrawlRequest)
        self.assertEqual(len(flow.active_steps), 4)

    def test_missing_flow_key_raises_missing_flow(self):
        with self.assertRaises(MissingFlowError):
            build_flow(_req(), {"title": {"name": "h1"}})

    def test_none_flow_raises_missing_flow(self):
        with self.assertRaises(MissingFlowError):
            build_flow(_req(), {"flow": None})

    def test_flow_not_a_list_rejected(self):
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt({"module": "get_metadata"}))

    def test_unknown_module_rejected(self):
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt([{"module": "eval"}]))

    def test_step_not_an_object_rejected(self):
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(["get_metadata"]))

    def test_step_without_module_rejected(self):
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt([{"params": {}}]))

    def test_unknown_step_field_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[0]["evil"] = 1
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_unknown_param_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[0] = {"module": "get_metadata", "params": {"exec": "import os"}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_code_object_in_param_rejected(self):
        register_module(
            "my_export",
            handler=lambda ctx, params: None,
            stage="export",
            allowed_params=frozenset({"payload"}),
        )
        steps = [
            {"module": "get_metadata"},
            {"module": "volumes_prepare"},
            {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
            {"module": "my_export", "params": {"payload": lambda: None}},
        ]
        try:
            with self.assertRaises(InvalidFlowError):
                build_flow(_req(), _fmt(steps))
        finally:
            REGISTRY.pop("my_export", None)

    def test_invalid_actions_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[2] = {"module": "crawl_chapter", "params": {"actions": ["download_image", "npm_exec"]}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_duplicate_actions_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[2] = {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content", "crawl_text_content"]}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_empty_actions_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[2] = {"module": "crawl_chapter", "params": {"actions": []}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_expected_selector_must_be_string(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[0] = {"module": "get_metadata", "params": {"expected_selector": 42}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_bad_url_param_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[0] = {"module": "get_metadata", "params": {"url": "not a url"}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_unhashable_action_has_deterministic_flow_error(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[2] = {"module": "crawl_chapter", "params": {"actions": [["not-an-action"]]}}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_params_must_be_object(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[0] = {"module": "get_metadata", "params": ["x"]}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_reordered_stages_rejected(self):
        steps = [
            {"module": "get_metadata"},
            {"module": "crawl_chapter"},
            {"module": "volumes_prepare"},
            {"module": "create_ebook"},
        ]
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_duplicate_stage_rejected(self):
        steps = [
            {"module": "get_metadata"},
            {"module": "get_metadata"},
            {"module": "volumes_prepare"},
            {"module": "crawl_chapter"},
            {"module": "create_ebook"},
        ]
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_missing_stage_rejected(self):
        for missing in range(4):
            steps = [dict(s) for i, s in enumerate(VALID_FLOW) if i != missing]
            with self.assertRaises(InvalidFlowError, msg=f"missing index {missing}"):
                build_flow(_req(), _fmt(steps))

    def test_flow_not_starting_with_get_metadata_rejected(self):
        steps = VALID_FLOW[1:] + [dict(VALID_FLOW[0])]
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))

    def test_flow_ending_without_export_rejected(self):
        steps = [dict(s) for s in VALID_FLOW]
        steps[-1] = {"module": "volumes_prepare"}
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), _fmt(steps))


class TestConditions(unittest.TestCase):
    def test_false_condition_on_required_module_is_rejected(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"eq": "gallery"}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_true_condition_keeps_step_active(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"eq": "novel"}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        flow = build_flow(_req(), fmt)
        self.assertFalse(flow.steps[0].skipped)
        self.assertEqual(len(flow.active_steps), 4)

    def test_in_membership_on_enum_field(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"in": ["novel", "comic"]}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        flow = build_flow(_req(), fmt)
        self.assertFalse(flow.steps[0].skipped)
        self.assertEqual(len(flow.active_steps), 4)

    def test_not_in_membership_cannot_skip_required_stage(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"not_in": ["novel"]}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_multiple_conditions_are_anded(self):
        fmt = _fmt(
            [
                {
                    "module": "get_metadata",
                    "when": {
                        "content_type": {"eq": "novel"},
                        "output_format": {"eq": "epub"},
                    },
                },
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        self.assertEqual(len(build_flow(_req(), fmt).active_steps), 4)
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(output_format=OutputFormat.PDF), fmt)

    def test_unknown_when_field_rejected(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"exec": {"eq": "true"}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_unknown_when_operator_rejected(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"gt": "novel"}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_when_in_requires_list(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"in": "novel"}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_when_expression_shape_rejected(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"and": [{"eq": "novel"}]}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_when_rejects_non_json_and_wrongly_typed_literals(self):
        for when in (
            {"content_type": {"eq": lambda: None}},
            {"headless": {"eq": 1}},
        ):
            with self.subTest(when=when):
                fmt = _fmt(
                    [
                        {"module": "get_metadata", "when": when},
                        {"module": "volumes_prepare"},
                        {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                        {"module": "create_ebook"},
                    ]
                )
                with self.assertRaises(InvalidFlowError):
                    build_flow(_req(), fmt)

    def test_format_content_type_and_actions_must_match_request(self):
        comic = _req(content_type="comic", output_format="cbz")
        with self.assertRaises(InvalidFlowError):
            build_flow(comic, _fmt(VALID_FLOW))
        comic_fmt = {
            "content_type": "comic",
            "flow": [
                {"module": "get_metadata"},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ],
        }
        with self.assertRaises(InvalidFlowError):
            build_flow(comic, comic_fmt)

    def test_invalid_fetch_configuration_fails_during_pure_flow_build(self):
        fmt = _fmt(VALID_FLOW)
        fmt["fetch"] = {"cloudflare": "yes"}
        with mock.patch("api.flow.RawPageService") as raw_page:
            with self.assertRaises(InvalidFlowError):
                build_flow(_req(), fmt)
        raw_page.assert_not_called()


class TestRuntimeInput(unittest.TestCase):
    def test_runtime_module_rejected_in_stored_flow(self):
        fmt = _fmt(
            [
                {"module": "get_user_input"},
                {"module": "get_metadata"},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_runtime_module_allowed_first_when_interactive(self):
        fmt = _fmt(
            [
                {"module": "get_metadata"},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        flow = build_flow(_req(), fmt, interactive=True)
        self.assertEqual(
            active_names(flow),
            ["get_user_input", "get_metadata", "volumes_prepare", "crawl_chapter", "create_ebook"],
        )

    def test_runtime_module_is_rejected_even_for_interactive_stored_flow(self):
        fmt = _fmt(
            [
                {"module": "get_user_input"},
                {"module": "get_metadata"},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt, interactive=True)

    def test_runtime_module_middle_rejected(self):
        fmt = _fmt(
            [
                {"module": "get_metadata"},
                {"module": "get_user_input"},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt, interactive=True)

    def test_runtime_module_duplicated_rejected(self):
        fmt = _fmt(
            [
                {"module": "get_user_input"},
                {"module": "get_user_input"},
                {"module": "get_metadata"},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter"},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt, interactive=True)


class TestExecuteFlow(unittest.TestCase):
    def setUp(self):
        self.runs = []
        self._pf_patcher = mock.patch("api.raw_page.PageFetcher")
        self.page_fetcher = self._pf_patcher.start()
        self.addCleanup(self._pf_patcher.stop)

        def record(name):
            def handler(ctx, params):
                self.runs.append(name)

            return handler

        set_module_handler("get_metadata", record("get_metadata"))
        set_module_handler("volumes_prepare", record("volumes_prepare"))
        set_module_handler("crawl_chapter", record("crawl_chapter"))
        set_module_handler("create_ebook", record("create_ebook"))
        self.addCleanup(_register_defaults)

    def test_executes_active_steps_in_order(self):
        flow = build_flow(_req(), _fmt(VALID_FLOW))
        ctx = CrawlContext(request=flow.request)
        execute_flow(flow, ctx)
        self.assertEqual(
            self.runs,
            ["get_metadata", "volumes_prepare", "crawl_chapter", "create_ebook"],
        )
        self.assertIsNone(ctx.raw_page)

    def test_execution_passes_request_pacer_to_raw_page_service(self):
        flow = build_flow(_req(sleep_ms=25), _fmt(VALID_FLOW))
        execute_flow(flow, CrawlContext(request=flow.request))

        before_request = self.page_fetcher.call_args.kwargs["before_request"]
        self.assertTrue(callable(before_request))

    def test_required_skipped_step_is_rejected_before_execution(self):
        fmt = _fmt(
            [
                {"module": "get_metadata", "when": {"content_type": {"eq": "gallery"}}},
                {"module": "volumes_prepare"},
                {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
                {"module": "create_ebook"},
            ]
        )
        with self.assertRaises(InvalidFlowError):
            build_flow(_req(), fmt)

    def test_interruption_closes_raw_page(self):
        flow = build_flow(_req(), _fmt(VALID_FLOW))

        def boom(ctx, params):
            raise KeyboardInterrupt

        set_module_handler("volumes_prepare", boom)
        ctx = CrawlContext(request=flow.request)
        with self.assertRaises(KeyboardInterrupt):
            execute_flow(flow, ctx)
        self.assertIsNone(ctx.raw_page)

    def test_failure_propagates_and_closes_raw_page(self):
        flow = build_flow(_req(), _fmt(VALID_FLOW))

        def boom(ctx, params):
            raise RuntimeError("nope")

        set_module_handler("volumes_prepare", boom)
        ctx = CrawlContext(request=flow.request)
        with self.assertRaises(RuntimeError):
            execute_flow(flow, ctx)
        self.assertIsNone(ctx.raw_page)

    def test_execution_rejects_mismatched_context_request_or_format(self):
        flow = build_flow(_req(), _fmt(VALID_FLOW))
        other_request = _req(content_type="comic", output_format="cbz")
        with self.assertRaises(InvalidFlowError):
            execute_flow(flow, CrawlContext(request=other_request))
        with self.assertRaises(InvalidFlowError):
            execute_flow(
                flow,
                CrawlContext(request=flow.request, format_definition={"content_type": "novel", "flow": []}),
            )

    def test_validated_steps_and_format_cannot_be_mutated_via_input(self):
        fmt = _fmt(copy.deepcopy(VALID_FLOW))
        flow = build_flow(_req(), fmt)
        fmt["flow"][2]["params"]["actions"].append("arbitrary_after_validation")
        self.assertEqual(flow.steps[2].params["actions"], ("crawl_text_content", "download_image"))
        with self.assertRaises(TypeError):
            flow.steps[2].params["actions"] += ("arbitrary_after_validation",)


class TestNoSideEffects(unittest.TestCase):
    def test_build_flow_never_creates_output_dir(self):
        import tempfile
        from pathlib import Path

        out = Path(tempfile.mkdtemp()) / "nope"
        request = _req(output_dir=str(out))
        flow = build_flow(request, _fmt(VALID_FLOW))
        self.assertFalse(out.exists())
        self.assertEqual(len(flow.active_steps), 4)

    def test_malformed_flows_make_zero_fetch_and_no_dir(self):
        malformed = [
            ("missing flow key", {"title": {}}),
            ("unknown module", _fmt([{"module": "eval"}])),
            ("bad ordering", _fmt([dict(s) for s in reversed(VALID_FLOW)])),
            ("duplicate", _fmt([dict(s) for s in VALID_FLOW] + [{"module": "create_ebook"}])),
        ]
        for label, fmt in malformed:
            with self.subTest(label=label), mock.patch(
                "api.raw_page.PageFetcher"
            ) as fake_pf, mock.patch("requests.get") as fake_get:
                with self.assertRaises((MissingFlowError, InvalidFlowError)):
                    build_flow(_req(), fmt)
                fake_pf.assert_not_called()
                fake_get.assert_not_called()

    def test_execute_requires_flow_instance(self):
        from api import build_flow as bf

        flow = bf(_req(), _fmt(VALID_FLOW))
        ctx = CrawlContext(request=flow.request)
        with self.assertRaises(TypeError):
            execute_flow("not a flow", ctx)

    def test_errors_are_pipeline_errors(self):
        from api.contracts import PipelineError

        self.assertTrue(issubclass(InvalidFlowError, PipelineError))
        self.assertTrue(issubclass(MissingFlowError, PipelineError))


if __name__ == "__main__":
    unittest.main()
