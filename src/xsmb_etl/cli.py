"""Command-line interface for local and R2-backed ETL operations."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from xsmb_etl.config import EtlEnvironment, Settings
from xsmb_etl.control import DrawStatus
from xsmb_etl.extract import ExtractedResult, ResultExtractor, parse_result_page
from xsmb_etl.lake_status import LakeStatus, inspect_lake
from xsmb_etl.logging_config import configure_logging
from xsmb_etl.marts import build_gold_tables
from xsmb_etl.migration import HistoricalMigrator
from xsmb_etl.pipeline import Pipeline
from xsmb_etl.quality import build_quality_report, require_quality
from xsmb_etl.r2 import R2ObjectStore
from xsmb_etl.repository import CentralDataLakeRepository, DataLakeRepository, SouthernDataLakeRepository
from xsmb_etl.run_models import LotteryRegion, SourceLineage
from xsmb_etl.storage import LocalObjectStore
from xsmb_etl.transform import canonical_results_from_frame, loto_daily_frame
from xsmb_etl.xsmn_extract import SouthernExtractedResult, SouthernResultExtractor, parse_southern_result_page
from xsmb_etl.xsmn_marts import build_southern_gold_tables
from xsmb_etl.xsmn_pipeline import SouthernPipeline
from xsmb_etl.xsmn_quality import build_southern_quality_report
from xsmb_etl.xsmn_transform import (
    canonical_southern_results_from_frame,
    southern_draw_results_frame,
    southern_loto_daily_frame,
)
from xsmb_etl.xsmt_extract import CentralResultExtractor, parse_central_result_page
from xsmb_etl.xsmt_pipeline import CentralPipeline
from xsmb_etl.xsmt_quality import build_central_quality_report


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)

    if args.command == 'run':
        regions = _regions(args.region)
        if args.fixture and len(regions) > 1:
            parser.error('--fixture requires one explicit region')
        results = []
        for region in regions:
            target_date = args.target_date or _default_target_date(region)
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            pipeline = _pipeline(region, repository, settings, fixture=args.fixture, target_date=target_date)
            results.append(pipeline.run(target_date, force=args.force))
        _print_models(results, collapse_single=True)
        return 0
    if args.command == 'backfill':
        regions = _regions(args.region)
        results = []
        for region in regions:
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            pipeline = _pipeline(region, repository, settings)
            results.extend(pipeline.backfill(args.from_date, args.to_date, force=args.force))
        print(_json_list(result.model_dump(mode='json') for result in results))
        return 0
    if args.command == 'build-gold':
        regions = _regions(args.region)
        results = []
        for region in regions:
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            results.append(_pipeline(region, repository, settings).build_gold())
        _print_models(results, collapse_single=True)
        return 0
    if args.command == 'validate':
        regions = _regions(args.region)
        if args.fixture and len(regions) > 1:
            parser.error('--fixture requires one explicit region')
        reports = {}
        for region in regions:
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            report = _validate(region, args, settings, repository)
            require_quality(report)
            reports[region.value] = report.model_dump(mode='json')
        if len(reports) == 1:
            print(_json_object(next(iter(reports.values()))))
        else:
            print(_json_object(reports))
        return 0
    if args.command == 'status':
        regions = _regions(args.region)
        statuses = []
        for region in regions:
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            statuses.append(inspect_lake(repository))
        if args.json:
            _print_status_json(statuses)
        else:
            _print_status_text(statuses)
        return 0 if all(status.healthy for status in statuses) else 1
    if args.command == 'download-gold':
        regions = _regions(args.region)
        paths = []
        for region in regions:
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            destination = args.download_output / region.value if len(regions) > 1 else args.download_output
            paths.extend(repository.download_gold(destination))
        print(_json_list(str(path) for path in paths))
        return 0
    if args.command == 'migrate-legacy':
        repository = _repository(
            settings,
            getattr(args, 'storage', None),
            LotteryRegion.XSMB,
            output=getattr(args, 'output', None),
        )
        result = HistoricalMigrator(repository).migrate(args.input, force=args.force)
        print(result.model_dump_json(indent=2))
        return 0
    if args.command == 'no-draw':
        regions = _regions(args.region)
        results = []
        for region in regions:
            repository = _repository_for_args(settings, args, region, multiple=len(regions) > 1)
            results.append(_pipeline(region, repository, settings).record_no_draw(args.target_date, detail=args.detail))
        _print_models(results, collapse_single=True)
        return 0
    parser.error(f'unsupported command: {args.command}')
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='lottery-etl',
        description='XSMB/XSMN/XSMT ETL with independent local or R2 data lakes',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    run = subparsers.add_parser('run', help='extract and publish one draw date')
    _add_storage_options(run)
    run.add_argument('--target-date', type=_date, help='YYYY-MM-DD; defaults to the latest completed draw')
    run.add_argument('--force', action='store_true', help='replace an existing successful date')
    run.add_argument('--fixture', type=Path, help='read saved HTML instead of contacting the source')

    backfill = subparsers.add_parser('backfill', help='process unresolved dates in a range')
    _add_storage_options(backfill)
    backfill.add_argument('--from', dest='from_date', type=_date, required=True)
    backfill.add_argument('--to', dest='to_date', type=_date, required=True)
    backfill.add_argument('--force', action='store_true')

    build_gold = subparsers.add_parser('build-gold', help='rebuild Gold from Silver')
    _add_storage_options(build_gold)

    validate = subparsers.add_parser('validate', help='run quality checks without publishing')
    _add_storage_options(validate)
    validate.add_argument('--fixture', type=Path)
    validate.add_argument('--target-date', type=_date)

    status = subparsers.add_parser(
        'status',
        help='check manifests and object metadata without scanning Silver or Gold data',
    )
    _add_storage_options(status)
    status.add_argument('--json', action='store_true', help='emit machine-readable JSON')

    download = subparsers.add_parser('download-gold', help='download current Gold objects')
    _add_storage_options(download)
    download.add_argument('--download-output', type=Path, required=True)

    migrate = subparsers.add_parser('migrate-legacy', help='migrate the historical wide JSON dataset')
    _add_storage_options(migrate, include_region=False)
    migrate.add_argument('--input', type=Path, default=Path('data/xsmb.json'))
    migrate.add_argument('--force', action='store_true')

    no_draw = subparsers.add_parser('no-draw', help='explicitly classify a date as no_draw')
    _add_storage_options(no_draw)
    no_draw.add_argument('--target-date', type=_date, required=True)
    no_draw.add_argument('--detail', required=True)
    return parser


def _add_storage_options(parser: argparse.ArgumentParser, *, include_region: bool = True) -> None:
    parser.add_argument('--storage', choices=('local', 'r2'))
    if include_region:
        parser.add_argument(
            '--region',
            choices=(LotteryRegion.XSMB.value, LotteryRegion.XSMN.value, LotteryRegion.XSMT.value, 'all'),
            default=LotteryRegion.XSMB.value,
            help='data lake to operate on; defaults to xsmb',
        )
    parser.add_argument('--output', type=Path, help='local object-store root; region defaults are used when omitted')


def _repository(
    settings: Settings,
    storage: str | None,
    region: LotteryRegion,
    *,
    output: Path | None = None,
) -> DataLakeRepository | SouthernDataLakeRepository | CentralDataLakeRepository:
    use_r2 = storage == 'r2' or (storage is None and output is None and settings.etl_env is EtlEnvironment.PRODUCTION)
    if use_r2:
        store = R2ObjectStore(settings, region=region)
    else:
        default_outputs = {
            LotteryRegion.XSMB: settings.local_output_dir,
            LotteryRegion.XSMN: settings.local_xsmn_output_dir,
            LotteryRegion.XSMT: settings.local_xsmt_output_dir,
        }
        default_output = default_outputs[region]
        store = LocalObjectStore(output or default_output)
    if region is LotteryRegion.XSMT:
        return CentralDataLakeRepository(store, gold_cache_control=settings.gold_cache_control)
    if region is LotteryRegion.XSMN:
        return SouthernDataLakeRepository(store, gold_cache_control=settings.gold_cache_control)
    return DataLakeRepository(store, gold_cache_control=settings.gold_cache_control)


def _repository_for_args(
    settings: Settings,
    args,
    region: LotteryRegion,
    *,
    multiple: bool,
) -> DataLakeRepository | SouthernDataLakeRepository | CentralDataLakeRepository:
    output = getattr(args, 'output', None)
    if output is not None and multiple:
        output = output / region.value
    storage = getattr(args, 'storage', None)
    if getattr(args, 'fixture', None) is not None and storage is None:
        storage = 'local'
    return _repository(settings, storage, region, output=output)


def _pipeline(
    region: LotteryRegion,
    repository: DataLakeRepository | SouthernDataLakeRepository | CentralDataLakeRepository,
    settings: Settings,
    *,
    fixture: Path | None = None,
    target_date: date | None = None,
):
    if fixture is not None and target_date is None:
        raise ValueError('target_date is required with a fixture')
    lineage = SourceLineage.TEST_FIXTURE if fixture else SourceLineage.LIVE_SOURCE
    if region in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
        if not isinstance(repository, SouthernDataLakeRepository):
            raise TypeError(f'{region.value.upper()} requires a station-grain repository')
        if fixture and target_date:
            extractor = _regional_fixture_extractor(fixture, target_date, settings, region)
        elif region is LotteryRegion.XSMT:
            extractor = CentralResultExtractor(settings)
        else:
            extractor = SouthernResultExtractor(settings)
        pipeline_class = CentralPipeline if region is LotteryRegion.XSMT else SouthernPipeline
        return pipeline_class(repository, extractor, source_lineage=lineage)
    if isinstance(repository, SouthernDataLakeRepository):
        raise TypeError('XSMB requires DataLakeRepository')
    extractor = (
        _fixture_extractor(fixture, target_date, settings) if fixture and target_date else ResultExtractor(settings)
    )
    return Pipeline(repository, extractor, source_lineage=lineage)


def _fixture_extractor(path: Path, target_date: date, settings: Settings):
    raw_response = path.read_bytes()
    source_url = ResultExtractor(settings).build_source_url(target_date)
    result = parse_result_page(raw_response, selected_date=target_date, source_url=source_url)

    class FixtureExtractor:
        def extract(self, selected_date: date) -> ExtractedResult:
            if selected_date != target_date:
                raise ValueError(f'fixture represents {target_date}, not {selected_date}')
            return ExtractedResult(raw_response=raw_response, result=result)

    return FixtureExtractor()


def _regional_fixture_extractor(
    path: Path,
    target_date: date,
    settings: Settings,
    region: LotteryRegion,
):
    raw_response = path.read_bytes()
    if region is LotteryRegion.XSMT:
        source_url = CentralResultExtractor(settings).build_source_url(target_date)
        result = parse_central_result_page(raw_response, selected_date=target_date, source_url=source_url)
    else:
        source_url = SouthernResultExtractor(settings).build_source_url(target_date)
        result = parse_southern_result_page(raw_response, selected_date=target_date, source_url=source_url)

    class FixtureExtractor:
        def extract(self, selected_date: date) -> SouthernExtractedResult:
            if selected_date != target_date:
                raise ValueError(f'fixture represents {target_date}, not {selected_date}')
            return SouthernExtractedResult(raw_response=raw_response, result=result)

    return FixtureExtractor()


def _validate(
    region: LotteryRegion,
    args,
    settings: Settings,
    repository: DataLakeRepository | SouthernDataLakeRepository | CentralDataLakeRepository,
):
    run_id = 'validation-only'
    if region in {LotteryRegion.XSMN, LotteryRegion.XSMT}:
        if not isinstance(repository, SouthernDataLakeRepository):
            raise TypeError(f'{region.value.upper()} requires a station-grain repository')
        if args.fixture:
            if args.target_date is None:
                raise ValueError('--target-date is required with --fixture')
            extracted = _regional_fixture_extractor(
                args.fixture,
                args.target_date,
                settings,
                region,
            ).extract(args.target_date)
            draw = southern_draw_results_frame([extracted.result], run_id)
            canonical = [extracted.result]
        else:
            draw = repository.read_all_silver_draw_results()
            if draw.empty:
                raise ValueError(f'no {region.value.upper()} Silver draw results are available')
            canonical = canonical_southern_results_from_frame(draw)
        loto = southern_loto_daily_frame(draw, run_id=run_id)
        statuses = {result.draw_date: DrawStatus.SUCCESS for result in canonical}
        gold = build_southern_gold_tables(draw, run_id=run_id, statuses=statuses)
        quality_builder = (
            build_central_quality_report if region is LotteryRegion.XSMT else build_southern_quality_report
        )
        return quality_builder(
            canonical,
            draw,
            loto,
            run_id=run_id,
            gold_tables=gold,
            statuses=statuses,
            today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
            **({'region': region} if region is LotteryRegion.XSMN else {}),
        )

    if args.fixture:
        if args.target_date is None:
            raise ValueError('--target-date is required with --fixture')
        extracted = _fixture_extractor(args.fixture, args.target_date, settings).extract(args.target_date)
        from xsmb_etl.transform import draw_results_frame

        draw = draw_results_frame([extracted.result], run_id)
        canonical = [extracted.result]
    else:
        draw = repository.read_all_silver_draw_results()
        if draw.empty:
            raise ValueError('no Silver draw results are available')
        canonical = canonical_results_from_frame(draw)
    loto = loto_daily_frame(draw, run_id=run_id)
    statuses = {result.draw_date: DrawStatus.SUCCESS for result in canonical}
    gold = build_gold_tables(draw, run_id=run_id, statuses=statuses)
    return build_quality_report(
        canonical,
        draw,
        loto,
        run_id=run_id,
        gold_tables=gold,
        statuses=statuses,
        today=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),
    )


def _regions(value: str) -> tuple[LotteryRegion, ...]:
    if value == 'all':
        return (LotteryRegion.XSMB, LotteryRegion.XSMN, LotteryRegion.XSMT)
    return (LotteryRegion(value),)


def _default_target_date(region: LotteryRegion) -> date:
    now = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    completed_at = {
        LotteryRegion.XSMB: time(18, 35),
        LotteryRegion.XSMN: time(16, 35),
        LotteryRegion.XSMT: time(17, 35),
    }[region]
    return now.date() if now.time() >= completed_at else now.date() - timedelta(days=1)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('date must use YYYY-MM-DD') from exc


def _json_list(values) -> str:
    import json

    return json.dumps(list(values), ensure_ascii=False, indent=2)


def _json_object(values: dict[str, Any]) -> str:
    import json

    return json.dumps(values, ensure_ascii=False, indent=2)


def _print_models(models, *, collapse_single: bool) -> None:
    if collapse_single and len(models) == 1:
        print(models[0].model_dump_json(indent=2))
        return
    print(_json_list(model.model_dump(mode='json') for model in models))


def _print_status_json(statuses: list[LakeStatus]) -> None:
    if len(statuses) == 1:
        print(statuses[0].model_dump_json(indent=2))
        return
    print(_json_object({status.region.value: status.model_dump(mode='json') for status in statuses}))


def _print_status_text(statuses: list[LakeStatus]) -> None:
    for index, status in enumerate(statuses):
        if index:
            print()
        state = 'OK' if status.healthy else 'UNHEALTHY'
        print(f'{status.region.value.upper()}  {state}')
        if status.run_id:
            print(f'  publication: {status.target_date} (run {status.run_id})')
            quality = 'passed' if status.quality_passed else 'failed'
            print(f'  control: {status.run_status or "missing"}; quality={quality}')
            print(
                f'  objects: {status.verified_object_count}/{status.object_count} metadata checks passed; '
                f'{status.total_size_bytes} bytes'
            )
            snapshot = 'matches latest' if status.snapshot_matches_latest else 'does not match latest'
            print(f'  snapshot: {snapshot}')
        for issue in status.issues:
            print(f'  issue: {issue}')
