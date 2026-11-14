// macOS JXA: give Chrome documents their own window; preserve focus by default.
// AppKit calls do not require Apple Events access to the browser.
ObjC.import("AppKit");

function run(args) {
  const focus = args.includes("--focus");
  const reuse = args.includes("--reuse");
  const workspace = $.NSWorkspace.sharedWorkspace;
  const previous = workspace.frontmostApplication;
  const url = $.NSURL.URLWithString(args[0]);
  const browserURL = workspace.URLForApplicationToOpenURL(url);
  if (!browserURL) throw new Error("No default browser is configured.");
  const browserID = ObjC.unwrap($.NSBundle.bundleWithURL(browserURL).bundleIdentifier);

  if (reuse) {
    // The preview itself receives a focus event over its existing connection.
    // Activate only the application here: opening its URL would create a copy.
    if (focus) {
      const applications = $.NSRunningApplication.runningApplicationsWithBundleIdentifier(browserID);
      if (applications.count) applications.objectAtIndex(0).activateWithOptions(2);
    }
    return;
  }

  const task = $.NSTask.alloc.init;
  task.launchPath = "/usr/bin/open";
  // Chrome handles --new-window through its existing normal browser session.
  // Other default browsers retain their standard URL-opening behavior.
  task.arguments = browserID.startsWith("com.google.Chrome")
    ? [...(focus ? [] : ["-g"]), "-n", "-a", ObjC.unwrap(browserURL.path), "--args", "--new-window", args[0]]
    : [...(focus ? [] : ["-g"]), args[0]];
  task.launch;
  task.waitUntilExit;
  if (task.terminationStatus !== 0) throw new Error("Could not open the preview URL.");

  if (focus || !previous || ObjC.unwrap(previous.bundleIdentifier) === browserID) return;

  // Chrome may activate after `open -g` returns. Watch briefly for that delayed
  // activation. Stop if the user switches to any other application.
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline && !previous.terminated) {
    const current = workspace.frontmostApplication;
    if (current.processIdentifier !== previous.processIdentifier) {
      if (ObjC.unwrap(current.bundleIdentifier) !== browserID) break;
      previous.activateWithOptions(2); // NSApplicationActivateIgnoringOtherApps
    }
    $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.05));
  }
}
