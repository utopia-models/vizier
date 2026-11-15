// park.rs: Alt+V hides Vizier, and Alt+V pops it back up (the deployment's issue 13798; it was Alt+V Alt+V until 2026-11-15). The reader asked for the option to minimize
// the window, and only the option: no rule about when Vizier shows (it is still on screen by default, every launch).
//
// The window keeper in main.rs un-minimizes Vizier every 2 s, so a minimize (the title bar, Win+D, Win+M) never stuck.
// While the reader has parked it with Alt+V, the keeper leaves the minimize alone. Content protection is not part
// of this: it is enforced on a parked window exactly as on a shown one.
//
// Pure logic only (no Tauri), so it is tested on any host: `rustc --edition 2021 --test app/src/park.rs && ./park`.
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// A parked window that is back on screen this long (restored from the taskbar) is no longer parked, and the keeper
/// owns it again. The grace covers the moment between "parked" and the minimize taking effect.
pub const GRACE: Duration = Duration::from_secs(3);

/// What the 2 s keeper does with the window this tick.
#[derive(Debug, PartialEq, Eq)]
pub enum Keep {
    /// Not parked: un-minimize, show, topmost, back in its slot (the old behaviour).
    Restore,
    /// Parked and minimized (or still inside the grace): leave it where the reader put it.
    Leave,
    /// Parked, but the reader brought it back some other way: unpark, then keep it as usual.
    Unpark,
}

/// `parked_for`: how long ago Alt+V parked the window, `None` when it is not parked.
pub fn keep(parked_for: Option<Duration>, minimized: bool) -> Keep {
    match parked_for {
        None => Keep::Restore,
        Some(_) if minimized => Keep::Leave,
        Some(d) if d >= GRACE => Keep::Unpark,
        Some(_) => Keep::Leave,
    }
}

/// What Alt+V does: park (minimize) a shown window, or unpark (restore) a parked one.
#[derive(Debug, PartialEq, Eq)]
pub enum Toggle {
    Minimize,
    Restore,
}

pub fn toggle(parked: bool, minimized: bool) -> Toggle {
    if parked || minimized {
        Toggle::Restore
    } else {
        Toggle::Minimize
    }
}

/// The app's state: when Alt+V parked the window (`None` = not parked), and the press detector.
pub static PARKED: Mutex<Option<Instant>> = Mutex::new(None);

pub fn parked_for(now: Instant) -> Option<Duration> {
    PARKED.lock().ok().and_then(|p| p.map(|t| now.saturating_duration_since(t)))
}

pub fn set_parked(at: Option<Instant>) {
    if let Ok(mut p) = PARKED.lock() {
        *p = at;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ms(n: u64) -> Duration {
        Duration::from_millis(n)
    }

    // The replay: the user minimized Vizier and the keeper put it straight back (main always restored).
    #[test]
    fn keeper_leaves_a_parked_window_minimized() {
        assert_eq!(keep(Some(ms(10)), true), Keep::Leave);
        assert_eq!(keep(Some(Duration::from_secs(3600)), true), Keep::Leave);
    }

    #[test]
    fn keeper_still_restores_an_unparked_window() {
        // no rule about when Vizier shows: by default it is kept on screen, as before
        assert_eq!(keep(None, true), Keep::Restore);
        assert_eq!(keep(None, false), Keep::Restore);
    }

    #[test]
    fn restored_from_the_taskbar_unparks_after_the_grace() {
        assert_eq!(keep(Some(ms(500)), false), Keep::Leave); // minimize not landed yet
        assert_eq!(keep(Some(GRACE), false), Keep::Unpark);
    }

    #[test]
    fn alt_v_toggles() {
        assert_eq!(toggle(false, false), Toggle::Minimize);
        assert_eq!(toggle(true, true), Toggle::Restore);
        assert_eq!(toggle(false, true), Toggle::Restore); // minimized some other way: bring it back
    }

    #[test]
    fn shared_state_round_trip() {
        let t = Instant::now();
        set_parked(Some(t));
        assert_eq!(parked_for(t + ms(5)), Some(ms(5)));
        set_parked(None);
        assert_eq!(parked_for(t), None);
    }
}
