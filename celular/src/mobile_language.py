"""Flet first-run language onboarding, before constructing the migration screen."""
import asyncio
import flet as ft
from rhyft_i18n import (LANGUAGES, LanguagePreference, detect_language,
                        language_order, set_language, tr,
                        FIRST_HOLD_MS, ROTATION_HOLD_MS, FADE_MS)


async def device_language(page):
    try:
        info = await asyncio.wait_for(page.get_device_info(), timeout=5)
        locales = ['-'.join(str(part) for part in (item.language_code,
                   item.script_code, item.country_code) if part) for item in info.locales]
        return detect_language(locales)
    except (TimeoutError, AttributeError, RuntimeError, ValueError, ft.FletException):
        return detect_language()


async def choose_language(page, preference, initial, detected, settings=False):
    selected = initial
    done = asyncio.Event()
    rotating = True
    heading = ft.Text(tr('Escolha seu idioma', language=initial), size=26,
                      weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)
    fade = ft.Container(content=heading, opacity=1, animate_opacity=FADE_MS)
    hint = ft.Text(tr('Você pode trocar o idioma nas configurações.', language=initial),
                   size=12, color='#a8afc0', text_align=ft.TextAlign.CENTER)
    error = ft.Text('', color='#ff718c', size=12)
    choice = ft.RadioGroup(content=ft.Column([]), value=initial)

    def select(event):
        nonlocal selected, rotating
        selected = choice.value
        rotating = False
        motion.value = False
        fade.opacity = 1
        heading.value = tr('Escolha seu idioma', language=selected)
        hint.value = tr('Você pode trocar o idioma nas configurações.', language=selected)
        confirm.content = tr('Salvar' if settings else 'Continuar', language=selected)
        motion.label = tr('Animar saudação', language=selected)
        page.update()

    def toggle(event):
        nonlocal rotating
        rotating = bool(motion.value)
        fade.opacity = 1
        heading.value = tr('Escolha seu idioma', language=selected)
        page.update()

    def save(event):
        try:
            preference.save(selected)
        except OSError:
            error.value = tr('Não foi possível salvar. Tente novamente.', language=selected)
            page.update()
            return
        done.set()

    choice.content = ft.Column([ft.Radio(value=code, label=LANGUAGES[code])
                                for code in language_order(detected)], spacing=2)
    choice.on_change = select
    motion = ft.Switch(label=tr('Animar saudação', language=initial), value=True, on_change=toggle)
    confirm = ft.Button(content=tr('Salvar' if settings else 'Continuar', language=initial), on_click=save)
    layout = ft.Column([
        ft.Text('RHYFT', size=30, weight=ft.FontWeight.BOLD),
        ft.Text('Your music. No borders.', color='#a8afc0'), fade,
        choice, hint, motion, error, confirm,
    ], spacing=14, horizontal_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO)

    def dismiss(event):
        nonlocal selected
        selected = None
        done.set()

    if settings:
        dialog = ft.AlertDialog(modal=True, content=ft.Container(content=layout, width=360),
                    actions=[ft.TextButton(content=tr('Cancelar'), on_click=dismiss)], on_dismiss=dismiss)
        page.show_dialog(dialog)
    else:
        page.bgcolor = '#080a10'
        page.theme_mode = ft.ThemeMode.DARK
        page.add(ft.SafeArea(expand=True, content=ft.Container(content=layout, padding=24, expand=True)))

    async def wait_or_done(seconds):
        try:
            await asyncio.wait_for(done.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def animate():
        order = language_order(initial)
        index = 0
        if await wait_or_done(FIRST_HOLD_MS / 1000):
            return
        while not done.is_set():
            if rotating:
                fade.opacity = 0
                page.update()
                if await wait_or_done(FADE_MS / 1000):
                    return
                if rotating:
                    index = (index + 1) % len(order)
                    heading.value = tr('Escolha seu idioma', language=order[index])
                fade.opacity = 1
                page.update()
            if await wait_or_done(ROTATION_HOLD_MS / 1000):
                return

    task = asyncio.create_task(animate())
    try:
        await done.wait()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if settings:
        if page.controls is not None:
            page.pop_dialog()
    else:
        page.clean()
    return selected


async def ensure_language(page, directory):
    preference = LanguagePreference(directory)
    saved = preference.read()
    if saved:
        set_language(saved)
        return
    detected = await device_language(page)
    selected = await choose_language(page, preference, detected, detected)
    set_language(selected)


async def open_language_settings(page, directory):
    preference = LanguagePreference(directory)
    detected = await device_language(page)
    selected = await choose_language(page, preference, preference.read() or detected, detected, settings=True)
    if selected:
        page.show_dialog(ft.AlertDialog(title=ft.Text(tr('Idioma', language=selected)),
             content=ft.Text(tr('Reinicie o app para aplicar o idioma.', language=selected)),
             actions=[ft.TextButton(content='OK', on_click=lambda e: page.pop_dialog())]))
