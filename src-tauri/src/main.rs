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
