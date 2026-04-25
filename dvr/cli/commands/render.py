"""``dvr render`` sub-commands."""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from ...resolve import Resolve
from .. import output

app = typer.Typer(
    name="render",
    help="Render queue: submit, watch, status, presets, formats, codecs.",
)


def _resolve(ctx: typer.Context) -> Resolve:
    cfg = ctx.obj or {}
    return Resolve(auto_launch=cfg.get("auto_launch", True), timeout=cfg.get("timeout", 30.0))


@app.command("queue")
def queue(ctx: typer.Context) -> None:
    """List jobs in the render queue."""
    r = _resolve(ctx)
    output.emit(r.render.queue(), fmt=ctx.obj["format"], headline="render queue")


@app.command("presets")
def presets(ctx: typer.Context) -> None:
    """List available render presets."""
    r = _resolve(ctx)
    output.emit(
        [{"name": n} for n in r.render.presets()],
        fmt=ctx.obj["format"],
        headline="presets",
    )


@app.command("formats")
def formats(ctx: typer.Context) -> None:
    """List render container formats."""
    r = _resolve(ctx)
    rows = [{"format": k, "extension": v} for k, v in r.render.formats().items()]
    output.emit(rows, fmt=ctx.obj["format"], headline="formats")


@app.command("codecs")
def codecs(
    ctx: typer.Context,
    format_name: Annotated[str, typer.Argument(help="Container format (e.g. mov, mxf).")],
) -> None:
    """List codecs available for a container format."""
    r = _resolve(ctx)
    rows = [{"codec": k, "label": v} for k, v in r.render.codecs(format_name).items()]
    output.emit(rows, fmt=ctx.obj["format"], headline=f"codecs ({format_name})")


@app.command("submit")
def submit(
    ctx: typer.Context,
    target_dir: Annotated[str, typer.Option("--target-dir", "-o", help="Output directory.")],
    custom_name: Annotated[
        str | None,
        typer.Option("--name", help="Custom output filename (without extension)."),
    ] = None,
    preset: Annotated[str | None, typer.Option("--preset", help="Render preset.")] = None,
    format: Annotated[
        str | None, typer.Option("--format-codec-format", help="Container format.")
    ] = None,
    codec: Annotated[str | None, typer.Option("--codec", help="Codec.")] = None,
    no_start: Annotated[
        bool,
        typer.Option("--no-start", help="Queue the job but do not start rendering."),
    ] = False,
    preflight: Annotated[
        bool,
        typer.Option(
            "--preflight",
            help="Run `dvr lint` first; abort if any errors are found.",
        ),
    ] = False,
    wait: Annotated[bool, typer.Option("--wait", help="Block until the render finishes.")] = False,
    stream: Annotated[
        bool,
        typer.Option(
            "--stream",
            help="Emit newline-delimited JSON progress events while waiting.",
        ),
    ] = False,
) -> None:
    """Configure and queue a render of the current timeline."""
    r = _resolve(ctx)

    if preflight:
        from ... import lint as lint_mod

        report = lint_mod.lint(r)
        if report.errors:
            output.emit(report.to_dict(), fmt=ctx.obj["format"], headline="preflight")
            typer.echo("preflight failed: render aborted.", err=True)
            raise typer.Exit(1)

    job = r.render.submit(
        target_dir=target_dir,
        custom_name=custom_name,
        preset=preset,
        format=format,
        codec=codec,
        start=not no_start,
    )

    if wait or stream:
        if stream:
            for event in r.render.watch([job.id]):
                sys.stdout.write(json.dumps(event) + "\n")
                sys.stdout.flush()
        elif sys.stdout.isatty():
            _watch_with_progress_bar(r, [job.id])
        else:
            job.wait()
        output.emit(job.inspect(), fmt=ctx.obj["format"], headline=f"job {job.id}")
        return

    output.emit({"job_id": job.id, "started": not no_start}, fmt=ctx.obj["format"])


def _watch_with_progress_bar(r: Resolve, job_ids: list[str]) -> None:
    """Render watch with a Rich progress bar — used when stdout is a TTY."""
    progress = Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("·"),
        TimeRemainingColumn(),
        console=Console(stderr=True),
    )
    tasks: dict[str, TaskID] = {}
    with progress:
        for jid in job_ids:
            short = jid[:8]
            tasks[jid] = progress.add_task(f"render {short}", total=100)
        for event in r.render.watch(job_ids):
            jid = event.get("job_id", "")
            task = tasks.get(jid)
            if task is None:
                continue
            etype = event.get("type")
            if etype == "progress":
                progress.update(task, completed=float(event.get("percent", 0)))
            elif etype == "complete":
                progress.update(task, completed=100, description=f"render {jid[:8]} ✓ complete")
            elif etype in ("failed", "cancelled"):
                progress.update(task, description=f"render {jid[:8]} ✗ {etype}")


@app.command("status")
def status(
    ctx: typer.Context,
    job_id: Annotated[str, typer.Argument(help="Render job ID.")],
) -> None:
    """Get the status of a render job."""
    r = _resolve(ctx)
    from ...render import RenderJob

    job = RenderJob(r.render, job_id)
    output.emit(job.inspect(), fmt=ctx.obj["format"], headline=f"job {job_id}")


@app.command("watch")
def watch(
    ctx: typer.Context,
    job_id: Annotated[
        str | None,
        typer.Argument(help="Job ID to watch; defaults to all jobs in the queue."),
    ] = None,
) -> None:
    """Stream newline-delimited JSON status events until renders complete."""
    r = _resolve(ctx)
    targets = [job_id] if job_id else None
    for event in r.render.watch(targets):
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()


@app.command("stop")
def stop(ctx: typer.Context) -> None:
    """Stop the active render."""
    r = _resolve(ctx)
    r.render.stop()
    output.emit({"stopped": True}, fmt=ctx.obj["format"])


@app.command("clear")
def clear(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Clear all jobs from the render queue."""
    if not yes:
        typer.confirm("Really delete all render jobs?", abort=True)
    r = _resolve(ctx)
    r.render.clear()
    output.emit({"cleared": True}, fmt=ctx.obj["format"])
