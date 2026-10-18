//! Pet window shell: position memory, click-through, dashboard window, tray.
//!
//! Click-through: once a window ignores cursor events it receives no mouse events at all,
//! so the webview cannot tell us when the cursor re-enters the pet. Instead the frontend
//! reports the pet/bubble rectangles (`set_hit_rects`) and a ~60Hz thread compares them
//! with the global cursor position, toggling `set_ignore_cursor_events` only on change.

use std::{
    fs,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, PhysicalPosition, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};

const SAVE_DEBOUNCE: Duration = Duration::from_millis(400);
const EDGE_MARGIN: i32 = 24;
const TASKBAR_ALLOWANCE: i32 = 56;

#[derive(Deserialize, Clone, Copy, Debug)]
struct HitRect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

#[derive(Default)]
struct Shared {
    rects: Mutex<Vec<HitRect>>,
    pending_pos: Mutex<Option<(PhysicalPosition<i32>, Instant)>>,
    glass: AtomicBool,
}

#[derive(Serialize, Deserialize)]
struct SavedPos {
    x: i32,
    y: i32,
}

fn pos_file(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_config_dir().ok()?;
    fs::create_dir_all(&dir).ok()?;
    Some(dir.join("pet-position.json"))
}

fn save_pos(app: &AppHandle, p: PhysicalPosition<i32>) {
    if let Some(path) = pos_file(app) {
        if let Ok(json) = serde_json::to_string(&SavedPos { x: p.x, y: p.y }) {
            let _ = fs::write(path, json);
        }
    }
}

fn load_pos(app: &AppHandle) -> Option<SavedPos> {
    let raw = fs::read_to_string(pos_file(app)?).ok()?;
    serde_json::from_str(&raw).ok()
}

/// True if at least 250px of the pet window lies within a connected monitor.
fn visible_on_some_monitor(app: &AppHandle, p: &SavedPos) -> bool {
    let (px, py) = (p.x, p.y);
    app.available_monitors()
        .map(|ms| {
            ms.iter().any(|m| {
                let (mp, ms) = (m.position(), m.size());
                px >= mp.x && py >= mp.y && px + 250 <= mp.x + ms.width as i32 && py + 250 <= mp.y + ms.height as i32
            })
        })
        .unwrap_or(false)
}

fn default_pos(app: &AppHandle, win: &WebviewWindow) -> PhysicalPosition<i32> {
    let size = win.outer_size().map(|s| (s.width as i32, s.height as i32)).unwrap_or((420, 520));
    if let Ok(Some(m)) = app.primary_monitor() {
        let (mp, ms) = (m.position(), m.size());
        return PhysicalPosition::new(
            (mp.x + ms.width as i32 - size.0 - EDGE_MARGIN).max(mp.x),
            (mp.y + ms.height as i32 - size.1 - EDGE_MARGIN - TASKBAR_ALLOWANCE).max(mp.y),
        );
    }
    PhysicalPosition::new(100, 100)
}

fn place_pet(app: &AppHandle, win: &WebviewWindow, use_saved: bool) {
    let pos = match use_saved.then(|| load_pos(app)).flatten() {
        Some(p) if visible_on_some_monitor(app, &p) => PhysicalPosition::new(p.x, p.y),
        _ => default_pos(app, win),
    };
    let _ = win.set_position(pos);
}

// ---------------- commands ----------------

#[tauri::command]
fn set_hit_rects(rects: Vec<HitRect>, state: State<Shared>) {
    *state.rects.lock().unwrap() = rects;
}

// MUST be `async`: on Windows, building a window from a sync command runs on the main thread and
// deadlocks WebView2 -> the new window stays a blank white rectangle forever.
#[tauri::command]
async fn open_dashboard(app: AppHandle) -> Result<(), String> {
    show_dashboard(&app)
}

/// Whether native glass (acrylic / vibrancy) was applied to the dashboard window.
#[tauri::command]
fn glass_enabled(state: State<Shared>) -> bool {
    state.glass.load(Ordering::Relaxed)
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

fn show_dashboard(app: &AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("dashboard") {
        let _ = w.unminimize();
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
        return Ok(());
    }
    let win = WebviewWindowBuilder::new(app, "dashboard", WebviewUrl::App("dashboard.html".into()))
        .title("Tutor Pet Dashboard")
        .inner_size(1180.0, 760.0)
        .min_inner_size(900.0, 600.0)
        .center()
        .decorations(false) // custom title bar in the UI
        .transparent(true)
        .shadow(true) // keeps native rounded corners/border on Windows 11
        .build()
        .map_err(|e| e.to_string())?;
    apply_glass(app, &win);
    Ok(())
}

/// Native translucent glass behind the (semi-transparent) UI. If unsupported, the UI falls back
/// to a near-opaque dark background (see `glass_enabled`).
fn apply_glass(app: &AppHandle, win: &WebviewWindow) {
    #[cfg(target_os = "windows")]
    let ok = window_vibrancy::apply_acrylic(win, Some((10, 12, 24, 110))).is_ok();
    #[cfg(target_os = "macos")]
    let ok = window_vibrancy::apply_vibrancy(win, window_vibrancy::NSVisualEffectMaterial::HudWindow, None, Some(12.0)).is_ok();
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let ok = {
        let _ = win;
        false
    };
    app.state::<Shared>().glass.store(ok, Ordering::Relaxed);
}

// ---------------- setup ----------------

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open dashboard", true, None::<&str>)?;
    let reset = MenuItem::with_id(app, "reset", "Reset pet position", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &reset, &quit])?;

    let mut tray = TrayIconBuilder::new()
        .tooltip("Tutor Pet")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                // tray events fire on the main thread: build the window off-thread (same deadlock as above)
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = show_dashboard(&app);
                });
            }
            "reset" => {
                if let Some(win) = app.get_webview_window("pet") {
                    place_pet(app, &win, false);
                    if let Ok(p) = win.outer_position() {
                        save_pos(app, p);
                    }
                }
            }
            "quit" => app.exit(0),
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn spawn_poller(app: AppHandle) {
    thread::spawn(move || {
        let mut ignoring: Option<bool> = None;
        loop {
            thread::sleep(Duration::from_millis(16));
            let Some(win) = app.get_webview_window("pet") else { continue };
            let shared = app.state::<Shared>();

            // 1) click-through
            let rects = shared.rects.lock().unwrap().clone();
            if let (Ok(cur), Ok(inner), Ok(scale)) =
                (app.cursor_position(), win.inner_position(), win.scale_factor())
            {
                let lx = (cur.x - inner.x as f64) / scale;
                let ly = (cur.y - inner.y as f64) / scale;
                let hit = rects
                    .iter()
                    .any(|r| lx >= r.x && lx <= r.x + r.w && ly >= r.y && ly <= r.y + r.h);
                let want_ignore = !hit;
                if ignoring != Some(want_ignore) && win.set_ignore_cursor_events(want_ignore).is_ok() {
                    ignoring = Some(want_ignore);
                }
            }

            // 2) debounced position save
            let due = {
                let mut pending = shared.pending_pos.lock().unwrap();
                match *pending {
                    Some((p, at)) if at.elapsed() >= SAVE_DEBOUNCE => {
                        *pending = None;
                        Some(p)
                    }
                    _ => None,
                }
            };
            if let Some(p) = due {
                save_pos(&app, p);
            }
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(Shared::default())
        .invoke_handler(tauri::generate_handler![set_hit_rects, open_dashboard, glass_enabled, quit_app])
        .setup(|app| {
            let handle = app.handle().clone();
            let pet = app.get_webview_window("pet").expect("pet window missing from tauri.conf.json");

            place_pet(&handle, &pet, true);
            let _ = pet.set_ignore_cursor_events(true); // click-through until hit rects arrive
            pet.show()?;
            let _ = pet.set_always_on_top(true);

            let h = handle.clone();
            pet.on_window_event(move |e| {
                if let WindowEvent::Moved(p) = e {
                    *h.state::<Shared>().pending_pos.lock().unwrap() = Some((*p, Instant::now()));
                }
            });

            build_tray(&handle)?;
            spawn_poller(handle);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
