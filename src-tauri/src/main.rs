// Synapse Tauri v2 desktop application entry point.
// This is a minimal shell that wraps the existing React/Vite frontend as a native app.
// The main window is declared in tauri.conf.json (app.windows, label "main") —
// creating it here too would panic at startup with "webview `main` already exists".
//
// ADR-0048 §T4c: tauri-plugin-notification initialised here so the frontend can
// call @tauri-apps/plugin-notification JS APIs (isPermissionGranted, requestPermission,
// sendNotification) for ingest-completion system notifications.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Show, unminimize and focus the main window — used by the tray icon click and "Apri" menu item.
fn show_main_window<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    use tauri::Manager;
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn main() {
    tauri::Builder::default()
        // tauri-plugin-http: routes JS fetch() calls through the native HTTP stack so
        // cross-origin requests (e.g. to a CF-Access-protected backend) bypass the
        // webview CORS/preflight machinery.  CF-Access-Client-Id/Secret headers trigger
        // a preflight OPTIONS that Cloudflare Access rejects with 403 in the webview;
        // the native path sends them directly, no preflight. [F15][ADR-0047]
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        // tauri-plugin-shell: used by ConvertPanel "Avvia Marker" button to spawn the
        // Marker microservice as a detached background process (user-initiated, user-configured
        // local path — same trust level as the user's own terminal). [F12][R12-6]
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![])
        .setup(|app| {
            #[cfg(debug_assertions)]
            {
                use tauri::Manager;
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }

            // System-tray (menu-bar) icon — stays in the macOS status bar while Synapse runs,
            // even when the window is minimized or closed. Left-click reopens the window; the
            // menu offers "Apri Synapse" / "Esci". [F15 desktop UX]
            {
                use tauri::menu::{Menu, MenuItem};
                use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

                let show_i = MenuItem::with_id(app, "tray_show", "Apri Synapse", true, None::<&str>)?;
                let quit_i = MenuItem::with_id(app, "tray_quit", "Esci", true, None::<&str>)?;
                let menu = Menu::with_items(app, &[&show_i, &quit_i])?;

                TrayIconBuilder::with_id("synapse-tray")
                    .icon(app.default_window_icon().expect("bundled window icon").clone())
                    .tooltip("Synapse")
                    .menu(&menu)
                    .show_menu_on_left_click(false)
                    .on_menu_event(|app, event| match event.id.as_ref() {
                        "tray_show" => show_main_window(app),
                        "tray_quit" => app.exit(0),
                        _ => {}
                    })
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        } = event
                        {
                            show_main_window(tray.app_handle());
                        }
                    })
                    .build(app)?;
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Synapse");
}
