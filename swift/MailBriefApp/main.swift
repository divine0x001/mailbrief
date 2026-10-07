import AppKit

// MARK: - Chemins

enum AppConfig {
    /// Racine du projet : injectée dans Info.plist à la compilation.
    /// Aucun chemin personnel en dur — surcharge possible via MAILBRIEF_ROOT.
    static let projectRoot: String = {
        if let env = ProcessInfo.processInfo.environment["MAILBRIEF_ROOT"],
           !env.isEmpty {
            return env
        }
        if let fromPlist = Bundle.main.object(forInfoDictionaryKey: "MailBriefProjectRoot") as? String,
           !fromPlist.isEmpty {
            return fromPlist
        }
        return ""
    }()

    static var isConfigured: Bool { !projectRoot.isEmpty }
    static var script: String { projectRoot.isEmpty ? "" : projectRoot + "/scripts/run.sh" }
    static var stateFile: String { projectRoot.isEmpty ? "" : projectRoot + "/data/state.json" }
    static var runLogFile: String { projectRoot.isEmpty ? "" : projectRoot + "/data/app-run.log" }
    static var launchdLog: String { projectRoot.isEmpty ? "" : projectRoot + "/data/launchd.log" }
    static var launchdPlist: String {
        NSHomeDirectory() + "/Library/LaunchAgents/com.mailbrief.daily.plist"
    }
}

// MARK: - Lecture de l'état / de la planification

struct Status {
    var lastRun: String = "jamais"
    var processed: Int = 0
    var runs: Int = 0
    var nextRun: String = "non planifié"
    var ollamaUp: Bool = false
}

func readStatus() -> Status {
    var status = Status()

    // Dernier run
    if let data = FileManager.default.contents(atPath: AppConfig.stateFile),
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
        status.runs = obj["runs"] as? Int ?? 0
        status.processed = (obj["processed"] as? [Any])?.count ?? 0
        if let iso = obj["last_run_iso"] as? String {
            let inFmt = DateFormatter()
            inFmt.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
            let outFmt = DateFormatter()
            outFmt.dateFormat = "dd/MM à HH:mm"
            if let date = inFmt.date(from: iso) {
                status.lastRun = outFmt.string(from: date)
            } else {
                status.lastRun = iso
            }
        }
    }

    // Heure planifiée
    if let data = FileManager.default.contents(atPath: AppConfig.launchdPlist),
       let obj = try? PropertyListSerialization.propertyList(
           from: data, options: [], format: nil) as? [String: Any],
       let cal = obj["StartCalendarInterval"] as? [String: Any],
       let hour = cal["Hour"] as? Int,
       let minute = cal["Minute"] as? Int {
        status.nextRun = String(format: "tous les jours à %02d:%02d", hour, minute)
    }

    // Ollama : on ne fait un appel réseau que quand le menu s'ouvre,
    // jamais en boucle — d'où le coût nul à l'inactivité.
    if let url = URL(string: "http://127.0.0.1:11434/api/tags") {
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.0
        if (try? URLSession.shared.sendSync(req)) != nil {
            status.ollamaUp = true
        }
    }
    return status
}

extension URLSession {
    /// Petit helper synchrone : utilisé uniquement à l'ouverture du menu.
    func sendSync(_ request: URLRequest) throws -> (Data, URLResponse) {
        var result: (Data, URLResponse)?
        var failure: Error?
        let sem = DispatchSemaphore(value: 0)
        let task = dataTask(with: request) { data, response, error in
            if let error = error { failure = error }
            else if let data = data, let response = response { result = (data, response) }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + 1.5)
        if let failure = failure { throw failure }
        guard let result = result else {
            throw NSError(domain: "MailBrief", code: -1,
                          userInfo: [NSLocalizedDescriptionKey: "timeout"])
        }
        return result
    }
}

// MARK: - Lancement du script

/// Construit le Process utilisé par le menu (et par le mode `--run`),
/// pour que les deux partagent exactement le même code.
func makeScriptProcess(_ args: [String]) -> Process {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/bash")
    p.arguments = [AppConfig.script] + args

    // Sortie vers fichier plutôt que pipe : pas de risque de blocage,
    // et on garde une trace pour le dépannage.
    FileManager.default.createFile(atPath: AppConfig.runLogFile, contents: nil)
    if let handle = try? FileHandle(forWritingTo: URL(fileURLWithPath: AppConfig.runLogFile)) {
        handle.seekToEndOfFile()
        p.standardOutput = handle
        p.standardError = handle
    }
    return p
}

func readRunLogTail(limit: Int = 700) -> String {
    guard let text = try? String(contentsOfFile: AppConfig.runLogFile,
                                  encoding: .utf8) else { return "" }
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard trimmed.count > limit else { return trimmed }
    return "…" + String(trimmed.suffix(limit))
}

// MARK: - Icône

/// Symboles réellement présents dans la bibliothèque SF Symbols.
/// (`envelope.badge.clock` n'existe pas : il renvoie nil et l'icône
/// disparaît silencieusement — d'où le repli ci-dessous.)
func statusSymbolName(busy: Bool) -> String {
    busy ? "envelope.fill" : "envelope.badge"
}

/// Applique l'icône en garantissant que l'item reste VISIBLE :
/// si le symbole manque, on retombe sur un emoji plutôt que
/// d'afficher un carré de largeur zéro.
func applyStatusIcon(to button: NSStatusBarButton, busy: Bool) {
    let name = statusSymbolName(busy: busy)
    if let image = NSImage(systemSymbolName: name,
                           accessibilityDescription: "MailBrief") {
        button.image = image
        button.title = ""
    } else {
        button.image = nil
        button.title = busy ? "✉️…" : "✉️"
    }
    button.appearsDisabled = busy
}

// MARK: - App

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var process: Process?
    private var busy = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            applyStatusIcon(to: button, busy: false)
            button.toolTip = "MailBrief — résumé quotidien des mails"
        }
        menu.delegate = self
        statusItem.menu = menu
        rebuildMenu()
    }

    // Rafraîchi à chaque ouverture du menu : pas de timer, donc 0 % CPU au repos.
    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuildMenu()
    }

    private func rebuildMenu() {
        menu.removeAllItems()
        let status = readStatus()

        let title = NSMenuItem(title: "MailBrief", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)

        let next = NSMenuItem(
            title: "Prochaine exécution : \(status.nextRun)",
            action: nil, keyEquivalent: ""
        )
        next.isEnabled = false
        menu.addItem(next)

        let last = NSMenuItem(
            title: "Dernier run : \(status.lastRun) · \(status.processed) mails traités",
            action: nil, keyEquivalent: ""
        )
        last.isEnabled = false
        menu.addItem(last)

        let ollama = NSMenuItem(
            title: "Ollama : \(status.ollamaUp ? "en marche" : "ARRÊTÉ")",
            action: nil, keyEquivalent: ""
        )
        ollama.isEnabled = false
        menu.addItem(ollama)

        menu.addItem(.separator())

        if busy {
            let running = NSMenuItem(title: "Exécution en cours…", action: nil,
                                     keyEquivalent: "")
            running.isEnabled = false
            menu.addItem(running)
            let stop = NSMenuItem(title: "Interrompre", action: #selector(stopRun),
                                  keyEquivalent: "")
            stop.target = self
            menu.addItem(stop)
        } else {
            let run = NSMenuItem(title: "Lancer le brief maintenant",
                                 action: #selector(runNow), keyEquivalent: "r")
            run.target = self
            menu.addItem(run)

            let preview = NSMenuItem(title: "Aperçu sans envoyer (dry-run)",
                                     action: #selector(runPreview), keyEquivalent: "d")
            preview.target = self
            menu.addItem(preview)

            let check = NSMenuItem(title: "Diagnostic", action: #selector(runCheck),
                                   keyEquivalent: "")
            check.target = self
            menu.addItem(check)
        }

        menu.addItem(.separator())

        let logs = NSMenuItem(title: "Ouvrir les logs", action: #selector(openLogs),
                              keyEquivalent: "")
        logs.target = self
        menu.addItem(logs)

        let folder = NSMenuItem(title: "Ouvrir le dossier du projet",
                                action: #selector(openFolder), keyEquivalent: "")
        folder.target = self
        menu.addItem(folder)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "Quitter MailBrief", action: #selector(quit),
                              keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        updateIcon(busy: busy)
    }

    private func updateIcon(busy: Bool) {
        guard let button = statusItem.button else { return }
        applyStatusIcon(to: button, busy: busy)
    }

    // MARK: Actions

    @objc private func runNow() { launch(args: [], label: "brief") }
    @objc private func runPreview() { launch(args: ["--dry-run"], label: "aperçu") }
    @objc private func runCheck() { launch(args: ["--check"], label: "diagnostic") }

    @objc private func stopRun() {
        process?.terminate()
    }

    private func launch(args: [String], label: String) {
        guard !busy else { return }
        guard AppConfig.isConfigured else {
            present(title: "Projet non configuré",
                    message: "Je ne connais pas la racine du projet.\n"
                        + "Recompile avec scripts/build_app.sh, ou définis "
                        + "MAILBRIEF_ROOT.")
            return
        }
        guard FileManager.default.isExecutableFile(atPath: AppConfig.script) else {
            present(title: "Script introuvable",
                    message: "Je ne trouve pas :\n\(AppConfig.script)")
            return
        }

        let p = makeScriptProcess(args)

        busy = true
        updateIcon(busy: true)
        process = p

        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self = self else { return }
                    self.busy = false
                    self.process = nil
                    self.updateIcon(busy: false)
                    let tail = self.logTail()
                    if status == 0 {
                        self.present(title: "MailBrief — \(label) terminé",
                                     message: tail.isEmpty
                                        ? "Succès. Le détail est dans data/app-run.log."
                                        : tail)
                    } else {
                        self.present(title: "MailBrief — échec (\(label))",
                                     message: "Code \(status)\n\n\(tail)")
                    }
                }
            }
        }

        do {
            try p.run()
        } catch {
            busy = false
            updateIcon(busy: false)
            present(title: "Impossible de lancer", message: error.localizedDescription)
        }
    }

    private func logTail(limit: Int = 700) -> String {
        readRunLogTail(limit: limit)
    }

    private func present(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        // Ouvre par-dessus les autres sans voler le focus au menu barre.
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func openLogs() {
        let url = URL(fileURLWithPath: AppConfig.launchdLog)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: Data())
        }
        NSWorkspace.shared.open(url)
    }

    @objc private func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: AppConfig.projectRoot))
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}

// MARK: - Point d'entrée

MainActor.assumeIsolated {
    // Mode sans interface : sert aux tests et à la ligne de commande.
    if CommandLine.arguments.contains("--status") {
        let s = readStatus()
        print("next_run=\(s.nextRun)")
        print("last_run=\(s.lastRun)")
        print("processed=\(s.processed)")
        print("runs=\(s.runs)")
        print("ollama=" + (s.ollamaUp ? "up" : "down"))
        print("project=\(AppConfig.projectRoot)")
        print("script_exists=\(FileManager.default.isExecutableFile(atPath: AppConfig.script))")
        exit(0)
    }

    // Mode sans interface : crée le vrai item de barre mesure et vérifie
    // qu'il a une image et une largeur — c'est ce qui a raté la première fois.
    if CommandLine.arguments.contains("--probe") {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        guard let button = item.button else {
            print("has_button=false")
            exit(1)
        }
        applyStatusIcon(to: button, busy: false)
        button.toolTip = "MailBrief"
        RunLoop.current.run(until: Date().addingTimeInterval(0.8))
        let width = button.frame.width
        print("symbol=\(statusSymbolName(busy: false))")
        print("image_loaded=\(button.image != nil)")
        print("fallback_title=\(button.title)")
        print("button_width=\(width)")
        print("tooltip=\(button.toolTip ?? "")")
        exit((button.image != nil || !button.title.isEmpty) && width > 1 ? 0 : 1)
    }

    // Mode sans interface : exécute le même process que le menu, en synchrone.
    if let idx = CommandLine.arguments.firstIndex(of: "--run") {
        let args = Array(CommandLine.arguments[(idx + 1)...])
        let p = makeScriptProcess(args)
        do {
            try p.run()
        } catch {
            FileHandle.standardError.write(Data("lancement impossible: \(error)\n".utf8))
            exit(127)
        }
        p.waitUntilExit()
        let tail = readRunLogTail()
        if !tail.isEmpty { print(tail) }
        exit(p.terminationStatus)
    }

    let app = NSApplication.shared
    app.setActivationPolicy(.accessory) // pas d'icône Dock
    let delegate = AppDelegate()
    app.delegate = delegate
    app.run()
}
