// Synapse Tauri v2 desktop application entry point.
// This is a minimal shell that wraps the existing React/Vite frontend as a native app.
// The main window is declared in tauri.conf.json (app.windows, label "main") —
// creating it here too would panic at startup with "webview `main` already exists".
//
// ADR-0048 §T4c: tauri-plugin-notification initialised here so the frontend can
// call @tauri-apps/plugin-notification JS APIs (isPermissionGranted, requestPermission,
// sendNotification) for ingest-completion system notifications.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![])
        .setup(|_app| {
            #[cfg(debug_assertions)]
            {
                use tauri::Manager;
                if let Some(window) = _app.get_webview_window("main") {
                    window.open_devtools();
                }
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Synapse");
}
