fn main() {
    // The build's own git sha + time, so the running app can say which commit it is (GET /state app_build on the
    // server): a detector compares it with main instead of guessing from a file date on the laptop.
    let sha = std::env::var("VIZIER_GIT_SHA").ok().filter(|s| !s.is_empty()).or_else(|| {
        std::process::Command::new("git").args(["rev-parse", "--short=12", "HEAD"]).output().ok()
            .filter(|o| o.status.success()).map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
    }).unwrap_or_else(|| "unknown".into());
    let built = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    println!("cargo:rustc-env=VIZIER_GIT_SHA={sha}");
    println!("cargo:rustc-env=VIZIER_BUILT_AT={built}");
    println!("cargo:rerun-if-changed=../.git/HEAD");
    println!("cargo:rerun-if-changed=../.git/refs/heads");
    println!("cargo:rerun-if-env-changed=VIZIER_GIT_SHA");
    tauri_build::build()
}
