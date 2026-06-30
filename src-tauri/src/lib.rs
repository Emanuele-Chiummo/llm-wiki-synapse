/// Synapse Tauri v2 library module.
///
/// This module provides the Tauri app builder configuration.
/// The actual application window and lifecycle are managed in main.rs.

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
