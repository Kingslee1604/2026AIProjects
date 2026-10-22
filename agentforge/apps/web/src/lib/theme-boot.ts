/** Where the chosen theme is remembered (this browser only). */
export const THEME_KEY = "agentforge-theme";

/** Runs in <head> before the first paint, so a light-theme user never sees a dark flash. */
export const THEME_BOOT_SCRIPT = `try{if(localStorage.getItem("${THEME_KEY}")==="light")document.documentElement.dataset.theme="light"}catch(e){}`;
