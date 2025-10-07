;;; emigo-core.el --- Entrypoints & commands for Emigo -*- lexical-binding: t; -*-

(require 'emigo-rpc)
(require 'emigo-index)
(require 'emigo-trace)

(defgroup emigo nil
  "Emigo agent settings."
  :group 'applications)

(defcustom emigo-user-config-dir user-emacs-directory
  "Path to the user's Emacs configuration root."
  :type 'string
  :group 'emigo)

(defcustom emigo-packages-dir (expand-file-name "elpa" user-emacs-directory)
  "Path to the directory containing downloaded Emacs packages."
  :type 'string
  :group 'emigo)

(defcustom emigo-server-policy 'attach-if-present
  "Policy for connecting to the Emacs server.
'attach-if-present: Connect to a running server, otherwise do nothing.
'spawn-if-missing: Start a new server if one is not running."
  :type '(choice (const attach-if-present) (const spawn-if-missing))
  :group 'emigo)

;;;###autoload
(defun emigo-start-session ()
  "Start or attach to an Emacs server and launch the Emigo agent."
  (interactive)
  (pcase emigo-server-policy
    ('attach-if-present
     (unless (server-running-p)
       (user-error "Emacs server is not running and emigo-server-policy is 'attach-if-present")))
    ('spawn-if-missing
     (unless (server-running-p)
       (server-start))))

  (emigo-index-build)
  (emigo-index-watch)

  (emigo-rpc-connect)
  (message "Emigo session started. Python agent process is starting...")

  ;; Open *Emigo* trace buffer.
  (with-current-buffer (get-buffer-create emigo-trace-buffer-name)
    (let ((inhibit-read-only t))
      (erase-buffer)
      (insert (format "Emigo session trace started at %s.\n\n" (format-time-string "%Y-%m-%d %H:%M:%S")))))
  (emigo-trace-open))

;;;###autoload
(defun emigo-instruct (goal)
  "Send a high-level GOAL to the Emigo planner."
  (interactive "MGoal: ")
  (emigo-rpc-notify "start_planner" goal))


(provide 'emigo-core)

;;; emigo-core.el ends here
