"""Record the sole Desktop UI with synthetic data, real orders and verified Replay.

Requires requirements-desktop.txt and requirements-demo.txt. Only this App's
window is captured. No saved user workspace, provider or credential is read.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def record(destination):
    import imageio_ffmpeg
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from tools.desktop.driver import DesktopDriver
    from sim_platform.examples.market_demo import write_fixture

    (ROOT / 'build').mkdir(exist_ok=True)
    with (
        TemporaryDirectory(prefix='desktop-video-', dir=ROOT / 'build') as staging,
        TemporaryDirectory(prefix='desktop-demo-') as temporary,
    ):
        output, root = Path(staging), Path(temporary)
        driver = DesktopDriver(root / 'workspace', write_fixture(root / 'fixture'))
        driver.window.resize(1440, 900)
        width, height, fps = 1440, 1000, 10
        encoder = subprocess.Popen(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                '-y',
                '-loglevel',
                'error',
                '-f',
                'rawvideo',
                '-vcodec',
                'rawvideo',
                '-pix_fmt',
                'rgb24',
                '-s',
                f'{width}x{height}',
                '-r',
                str(fps),
                '-i',
                '-',
                '-an',
                '-c:v',
                'libx264',
                '-preset',
                'fast',
                '-crf',
                '24',
                '-pix_fmt',
                'yuv420p',
                '-movflags',
                '+faststart',
                str(output / 'demo.mp4'),
            ],
            stdin=subprocess.PIPE,
        )
        count, last_frame = 0, 0.0
        chapter, caption = 'MARKET LAB', 'Historical market simulation · temporary synthetic data'
        captions = []
        evidence = dict(synthetic_data=True, model_calls=0)
        started = time.monotonic()

        def frame():
            nonlocal count, last_frame
            now = time.monotonic()
            if now - last_frame < 1 / fps:
                return
            last_frame = now
            image = QImage(width, height, QImage.Format.Format_RGB888)
            image.fill(QColor('#f7f8fa'))
            painter = QPainter(image)
            painter.drawPixmap(QRect(0, 0, width, 900), driver.window.grab())
            painter.fillRect(0, 900, width, 100, QColor('#172333'))
            painter.setPen(QColor('#a8c5f5'))
            painter.setFont(QFont('Helvetica Neue', 11, QFont.Weight.DemiBold))
            painter.drawText(QRect(28, 912, 1380, 22), Qt.AlignmentFlag.AlignLeft, chapter)
            painter.setPen(QColor('#ffffff'))
            painter.setFont(QFont('Helvetica Neue', 15))
            painter.drawText(QRect(28, 940, 1380, 50), Qt.TextFlag.TextWordWrap, caption)
            painter.end()
            raw = image.bits()
            encoder.stdin.write(bytes(raw[: image.sizeInBytes()]))
            count += 1
            if chapter.startswith('03') and not (output / 'demo-poster.jpg').exists():
                image.save(str(output / 'demo-poster.jpg'))

        driver.on_poll = frame

        def scene(title, text, seconds=2):
            nonlocal chapter, caption
            if captions:
                captions[-1]['end'] = count / fps
            chapter, caption = title, text
            captions.append(dict(chapter=title, start=count / fps, end=0, text=text))
            end = time.monotonic() + seconds
            driver.until(lambda: time.monotonic() >= end)

        try:
            driver.until(lambda: bool(driver.window.catalogue.get('accounts')))
            driver.until(lambda: driver.js('document.querySelectorAll(".nav").length') > 0)
            driver.click('[data-action="data"]')
            driver.click('.recording-toggle')
            scene(
                '01 / MARKET DATA',
                'Select a local recording. Expand its row to inspect the file or create an experiment.',
            )
            driver.click('[data-action="new"]')
            scene(
                '02 / PREPARE',
                'Choose your market, decision maker and independent simulated account. No broker is connected.',
            )
            driver.js("document.querySelector('#setup-form').requestSubmit()")
            driver.js(
                "document.querySelector('[name=execution_mode]').value='live';"
                "document.querySelector('#setup-form').requestSubmit()"
            )
            driver.js("document.querySelector('#setup-form').requestSubmit()")
            driver.until(
                lambda: any(k != 'manager' and v.get('run') for k, v in driver.window.views.items())
            )
            driver.identity = next(
                k for k, v in driver.window.views.items() if k != 'manager' and v.get('run')
            )
            driver.until(lambda: driver.js('document.querySelectorAll("#order-form").length') == 1)
            scene(
                '03 / TRADING WORKSPACE',
                'The experiment opens paused. Inspect market depth and the order estimate before submitting.',
            )
            driver.click('[data-action="play"]')
            driver.until(lambda: driver.view.get('pipeline', {}).get('state') == 'running')
            driver.js(
                "document.querySelector('[name=quantity]').value='10';"
                "document.querySelector('[name=limit_price]').value='190.10';"
                "document.querySelector('#order-form').requestSubmit()"
            )
            driver.until(lambda: bool(driver.view['account']['orders']))
            driver.until(lambda: any(e['kind'] == 'fill' for e in driver.view['events']))
            driver.click('[data-action="pause"]')
            driver.click('[data-action="records:fills"]')
            evidence['filled'] = True
            scene(
                '04 / INSPECT THE RECORD',
                'The real simulation records the fill and fees. Market updates preserve unfinished order inputs.',
            )
            driver.js('window.confirm=()=>true')
            driver.click('[data-action="stop-confirm"]')
            driver.until(lambda: driver.js('document.querySelectorAll(".result-grid").length') == 1)
            scene(
                '05 / SAVED RESULTS',
                'End the experiment to save its result. This report is read-only; opening it does not resume trading.',
            )
            run_id = driver.view['run']['id']
            log = next((root / 'workspace').rglob(run_id + '.jsonl'))
            result = []

            def received(encoded):
                event = json.loads(encoded)
                if event['kind'] == 'replay':
                    result.append(event['value'])

            driver.window.browser.commands.event.connect(received)
            driver.click('[data-action="replay"]')
            driver.js(
                "document.querySelector('#replay-form [name=path]').value="
                + json.dumps(str(log))
                + ";document.querySelector('#replay-form').requestSubmit()"
            )
            driver.until(lambda: bool(result))
            assert (
                result[0]['matched']
                and result[0]['events_verified']
                and result[0]['model_calls'] == 0
            ), result
            evidence.update(replay_matched=True, replay_events_verified=True, replay_model_calls=0)
            scene(
                '06 / VERIFIED REPLAY',
                'Inspect, seek or play the verified financial events. The Agent and model are not called again.',
                3,
            )
            driver.click('[data-action="agents"]')
            scene(
                'NEXT / AGENTS',
                'Create a versioned Agent, prepare its Python environment, and use it in the same trading workspace.',
            )
            captions[-1]['end'] = count / fps
        finally:
            driver.on_poll = None
            driver.close()
            encoder.stdin.close()
            encoder.wait(timeout=60)
        if encoder.returncode:
            raise RuntimeError('Video encoder failed')

        def stamp(value):
            ms = round(value * 1000)
            return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'

        (output / 'demo.en.vtt').write_text(
            'WEBVTT\n\n'
            + '\n\n'.join(
                f"{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for c in captions
            )
            + '\n'
        )
        evidence.update(
            duration_seconds=count / fps,
            frames=count,
            fps=fps,
            captions=captions,
            wall_seconds=round(time.monotonic() - started, 2),
        )
        (output / 'demo-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
        destination.mkdir(parents=True, exist_ok=True)
        for name in ('demo.mp4', 'demo-poster.jpg', 'demo.en.vtt', 'demo-evidence.json'):
            (output / name).replace(destination / name)
        print(json.dumps({k: v for k, v in evidence.items() if k != 'captions'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/site/media')
    args, _ = parser.parse_known_args()
    record(args.output.resolve())
