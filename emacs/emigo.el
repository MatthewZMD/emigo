;;; emigo.el --- Emigo  -*- lexical-binding: t -*-

;; Filename: emigo.el
;; Description: Emigo
;; Authors: Mingde (Matthew) Zeng <matthewzmd@posteo.net>
;;          Andy Stewart <lazycat.manatee@gmail.com>
;; Maintainer: Mingde (Matthew) Zeng <matthewzmd@posteo.net>
;;             Andy Stewart <lazycat.manatee@gmail.com>
;; Copyright (C) 2025, Emigo, all rights reserved.
;; Created: 2025-03-29
;; Version: 0.5
;; Last-Updated: Wed Oct  8 11:43:33 2025 (+0800)
;;           By: Mingde (Matthew) Zeng
;; Package-Requires: ((emacs "26.1") (transient "0.3.0") (compat "30.0.2.0") (markdown-mode "2.6"))
;; Keywords: ai emacs llm aider ai-pair-programming tools
;; URL: https://github.com/MatthewZMD/emigo
;; SPDX-License-Identifier: Apache-2.0
;;

;;; This file is NOT part of GNU Emacs

;;; Commentary:

;; Emigo

;;; Code:
(require 'cl-lib)
(require 'json)
(require 'map)
(require 'seq)
(require 'subr-x)
(require 'vc-git)
(require 'emigo-epc)
(require 'emigo-rpc)

(defgroup emigo nil
  "Emigo agent settings."
  :group 'applications)

(defcustom emigo-mode-hook '()
  "emigo mode hook."
  :type 'hook
  :group 'emigo)

(defcustom emigo-model "openrouter/deepseek/deepseek-chat-v3.1"
  "Default AI model.")

(defcustom emigo-base-url "https://openrouter.ai/api/v1"
  "Base URL for AI model.")

(defcustom emigo-api-key (getenv "OPENROUTER_API_KEY")
  "API key for AI model.")

(defcustom emigo-config-location (expand-file-name (locate-user-emacs-file "emigo/"))
  "Directory where emigo will store configuration files."
  :type 'directory)

(defcustom emigo-user-config-dir user-emacs-directory
  "Path to the user's Emacs configuration root."
  :type 'string
  :group 'emigo)

(defcustom emigo-packages-dir (expand-file-name "elpa" user-emacs-directory)
  "Path to the directory containing downloaded Emacs packages."
  :type 'string
  :group 'emigo)

(defcustom emigo-server-policy 'spawn-if-missing
  "Policy for connecting to the Emacs server.
'attach-if-present: Connect to a running server, otherwise do nothing.
'spawn-if-missing: Start a new server if one is not running."
  :type '(choice (const attach-if-present) (const spawn-if-missing))
  :group 'emigo)

;;;###autoload
(defun emigo-start-process ()
  "Start or attach to an Emacs server and launch the Emigo agent."
  (interactive)
  (pcase emigo-server-policy
    ('attach-if-present
     (unless (server-running-p)
       (user-error "Emacs server is not running and emigo-server-policy is 'attach-if-present")))
    ('spawn-if-missing
     (unless (server-running-p)
       (server-start))))

  (emigo-rpc-connect)
  (message "Emigo session started. Python agent process is starting..."))

;;;###autoload
(defun emigo-instruct (goal)
  "Send a high-level GOAL to the Emigo planner.
This determines the project root, ensures the Emigo buffer is visible,
inserts the GOAL as a prompt, and sends it."
  (interactive "MGoal: ")
  ;; Ensure the relevant emigo buffer is created and visible.
  ;; This also switches to that buffer.
  (emigo)
  ;; Now, insert the goal into the prompt area and send it.
  (let ((inhibit-read-only t))
    (goto-char (point-max))
    (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
      (forward-char (length emigo-prompt-symbol))
      (delete-region (point) (point-max))
      (insert goal)))
  (emigo-send-prompt))

(defun emigo--is-emigo-buffer-p (&optional buffer)
  "Return non-nil if BUFFER (defaults to current) is an Emigo buffer."
  (with-current-buffer (or buffer (current-buffer))
    (string-match-p "^\\*emigo:.*\\*$" (buffer-name))))

(defun emigo-project-root ()
  "Get the project root using VC-git, or fallback to file directory.
This function tries multiple methods to determine the project root."
  (or (vc-git-root default-directory)
      (when buffer-file-name
        (file-name-directory buffer-file-name))
      default-directory))

(defun emigo-select-buffer-name ()
  "Select an existing emigo session buffer.
If there is only one emigo buffer, return its name.
If there are multiple, prompt to select one interactively.
Returns nil if no emigo buffers exist.
This is used when you want to target an existing session."
  (let* ((buffers (seq-filter #'emigo--is-emigo-buffer-p (buffer-list)))
         (buffer-names (mapcar #'buffer-name buffers)))
    (pcase buffers
      (`() nil)
      (`(,name) (buffer-name name))
      (_ (completing-read "Select emigo session: " buffer-names nil t)))))

(defun emigo-get-buffer-name (&optional use-existing session-path)
  "Generate or find the emigo buffer name based on the session path.
If USE-EXISTING is non-nil, try to find an existing buffer.
SESSION-PATH defaults to the path determined by `emigo-project-root` or `default-directory`.
Searches parent directories for existing sessions."
  (let* ((current-session-path (file-truename (or session-path
                                                  (if current-prefix-arg ;; Check prefix arg for context
                                                      default-directory
                                                    (emigo-project-root)))))
         (target-buffer-name (format "*emigo:%s*" current-session-path)))
    (if use-existing
        (or (and (get-buffer target-buffer-name) target-buffer-name) ;; Exact match first
            ;; Search parent directories for existing sessions
            (let* ((emigo-buffers (seq-filter #'emigo--is-emigo-buffer-p (buffer-list)))
                   (buffer-session-paths
                    (mapcar (lambda (buf)
                              (when (string-match "^\\*emigo:\\(.*?\\)\\*$" (buffer-name buf))
                                (match-string 1 (buffer-name buf))))
                            emigo-buffers))
                   ;; Find closest parent directory that has an emigo session
                   (closest-parent-session-path
                    (car (sort (seq-filter (lambda (path)
                                             (and path
                                                  (file-in-directory-p current-session-path path)
                                                  (file-exists-p path)))
                                           buffer-session-paths)
                               (lambda (a b) (> (length a) (length b))))))) ;; Sort by length (deepest first)
              (when closest-parent-session-path
                (format "*emigo:%s*" closest-parent-session-path)))
            (emigo-select-buffer-name)) ;; Fallback to interactive selection if no match found
      ;; Not using existing, just return the calculated name
      target-buffer-name)))

(defvar emigo-project-buffers nil) ;; Keep track of buffer objects

(defvar-local emigo-session-path nil ;; Buffer-local session path
  "The session path (project root or current dir) associated with this Emigo buffer.")

(defvar-local emigo--llm-output "" ;; Buffer-local LLM output accumulator
  "Accumulates the LLM output stream for the current interaction.")

(defvar-local emigo-chat-file-info nil
  "String displaying info about files in chat context (e.g., '3 files [1234 tokens]').")

(defcustom emigo-python-command (if (memq system-type '(cygwin windows-nt ms-dos)) "python.exe" "python3")
  "The Python interpreter used to run the Emigo agent."
  :type 'string)

(defcustom emigo-enable-debug nil
  "If you got segfault error, please turn this option.
Then Emigo will start by gdb, please send new issue with `emigo-name' buffer content when next crash."
  :type 'boolean)

(defcustom emigo-enable-log nil
  "Enable this option to print log message in `emigo-name' buffer, default only print message header."
  :type 'boolean)

(defcustom emigo-enable-profile nil
  "Enable this option to output performance data to ~/emigo.prof."
  :type 'boolean)

(defcustom emigo-prompt-symbol "Emigo> "
  "The prompt string used in Emigo buffers."
  :type 'string
  :group 'emigo)

(defun emigo--user-emacs-directory ()
  "Get lang server with project path, file path or file extension."
  (expand-file-name user-emacs-directory))

(defun emigo--ensure-session-path (session-path)
  "Ensure SESSION-PATH is valid, erroring if nil."
  (unless session-path
    (error "[Emigo] Could not determine session path"))
  session-path)

(defun emigo-restart-process ()
  "Stop and restart Emigo process."
  (interactive)
  (when (and emigo--process (process-live-p emigo--process))
    (kill-process emigo--process))
  (setq emigo--process nil)
  (setq emigo--connection nil)
  (emigo-rpc-connect)
  (message "[Emigo] Process restarted."))

(defun emigo-enable ()
  (add-hook 'post-command-hook #'emigo-rpc-connect))

(defun emigo-update-header-line (session-path)
  (setq header-line-format (concat
                            (propertize (format " Project [%s]" (emigo-format-session-path session-path)) 'face font-lock-constant-face)
                            (when emigo-chat-file-info
                              (propertize (format " | %s" emigo-chat-file-info) 'face font-lock-constant-face)))))

(defun emigo-update-chat-files-info (session-path chat-files)
  (let ((buffer (get-buffer (emigo-get-buffer-name t session-path)))) ;; Find existing buffer
    (with-current-buffer buffer
      (setq-local emigo-chat-file-info chat-files)))
  (emigo-update-header-line session-path))

(defun emigo-shrink-dir-name (input-string)
  (let* ((words (split-string input-string "-"))
         (abbreviated-words (mapcar (lambda (word) (substring word 0 (min 1 (length word)))) words)))
    (mapconcat 'identity abbreviated-words "-")))

(defun emigo-format-session-path (session-path)
  (let* ((file-path (split-string session-path "/" t))
         (full-num 2)
         (show-name nil)
         shown-path)
    (setq show-path
          (if buffer-file-name
              (if show-name file-path (butlast file-path))
            file-path))
    (setq show-path (nthcdr (- (length show-path)
                               (if buffer-file-name
                                   (if show-name (1+ full-num) full-num)
                                 (1+ full-num)))
                            show-path))
    ;; Shrink parent directory name to save minibuffer space.
    (setq show-path
          (append (mapcar #'emigo-shrink-dir-name (butlast show-path))
                  (last show-path)))
    ;; Join paths.
    (setq show-path (mapconcat #'identity show-path "/"))
    show-path))

;; Define the main entry point command
;;;###autoload
(defun emigo ()
  "Start or switch to an Emigo session.
With no prefix arg, uses the project root (e.g., Git root) as the session path.
With a prefix arg (C-u), uses the current directory (`default-directory`)
as the session path."
  (interactive)
  (if (emigo-buffer-exist-p emigo-buffer)
      (emigo-open)
    (let* ((session-path (if current-prefix-arg
                             (file-truename default-directory)
                           (file-truename (emigo-project-root))))
           (buffer-name (emigo-get-buffer-name nil session-path))
           (buffer (get-buffer-create buffer-name)))
      ;; Ensure RPC process is running or starting
      (unless (and emigo--process (process-live-p emigo--process))
        (emigo-rpc-connect))

      ;; Set buffer-local session path variable
      (with-current-buffer buffer
        (emigo-mode)

        (emigo-update-header-line session-path)
        (setq-local emigo-session-path session-path))

      ;; Add buffer to tracked list
      (add-to-list 'emigo-project-buffers buffer t) ;; Use t to avoid duplicates

      (advice-add 'delete-other-windows :around #'emigo--advice-delete-other-windows)
      (advice-add 'other-windows :around #'emigo--advice-other-window)

      ;; Switch to or display the buffer
      (emigo-create-window buffer) ;; Use the specific buffer

      ;; Insert prompt.
      (insert (propertize (concat "\n\n" emigo-prompt-symbol) 'face font-lock-keyword-face)))))

;; --- Dedicated Window Width Enforcement ---

(defcustom emigo-window-width 50
  "The width of `emigo' dedicated window."
  :type 'integer
  :group 'emigo)

(defvar emigo-window nil
  "The dedicated `emigo' window.")

(defvar emigo-buffer nil
  "The dedicated `emigo' buffer.")

(defvar emigo-window-resize-timer nil
  "Timer to periodically enforce the dedicated window width.")

(defun emigo-ensure-window-width ()
  "Restore the saved width of emigo dedicated window."
  (when (and (emigo-exist-p)
             (window-live-p emigo-window)
             (not (= (window-width emigo-window) emigo-window-width)))
    (window-resize emigo-window
                   (- emigo-window-width (window-width emigo-window))
                   t)))

(defun emigo--start-dedicated-window-timer ()
  "Start the timer to enforce the dedicated window width."
  (emigo--cancel-dedicated-window-timer) ;; Cancel existing timer first
  (setq emigo-window-resize-timer
        (run-with-timer 1 1 #'emigo-ensure-window-width))) ;; Check every 1 second

(defun emigo--cancel-dedicated-window-timer ()
  "Cancel the timer that enforces the dedicated window width."
  (when (timerp emigo-window-resize-timer)
    (cancel-timer emigo-window-resize-timer))
  (setq emigo-window-resize-timer nil))

(defun emigo-current-window-take-height (&optional window)
  "Return the height the `window' takes up.
Not the value of `window-width', it returns usable rows available for WINDOW.
If `window' is nil, get current window."
  (let ((edges (window-edges window)))
    (- (nth 3 edges) (nth 1 edges))))

(defun emigo-exist-p ()
  (and (emigo-buffer-exist-p emigo-buffer)
       (emigo-window-exist-p emigo-window)))

(defun emigo-window-exist-p (window)
  "Return `non-nil' if WINDOW exist.
Otherwise return nil."
  (and window (window-live-p window)))

(defun emigo-buffer-exist-p (buffer)
  "Return `non-nil' if `BUFFER' exist.
Otherwise return nil."
  (and buffer (buffer-live-p buffer)))

(defun emigo-close ()
  "Close dedicated `emigo' window."
  (interactive)
  (if (emigo-exist-p)
      (let ((current-window (selected-window)))
        ;; Cancel the resize timer first
        (emigo--cancel-dedicated-window-timer)
        ;; Remember height.
        (emigo-select-window)
        (delete-window emigo-window)
        (setq emigo-window nil) ;; Clear the variable
        (if (emigo-window-exist-p current-window)
            (select-window current-window)))
    (message "`EMIGO DEDICATED' window does not exist.")))

(defun emigo-toggle ()
  "Toggle dedicated `emigo' window."
  (interactive)
  (if (emigo-exist-p)
      (emigo-close)
    (emigo-open)))

(defun emigo-open ()
  "Open dedicated `emigo' window."
  (interactive)
  (if (emigo-window-exist-p emigo-window)
      (emigo-select-window)
    (emigo-pop-window)))

(defun emigo-pop-window ()
  "Pop emigo dedicated window if it exists."
  (setq emigo-window (display-buffer (current-buffer) `(display-buffer-in-side-window (side . right) (window-width . ,emigo-window-width))))
  (select-window emigo-window)
  (set-window-buffer emigo-window emigo-buffer)
  (set-window-dedicated-p (selected-window) t)
  ;; Start the enforcement timer
  (emigo--start-dedicated-window-timer))

(defun emigo-select-window ()
  "Select emigo dedicated window."
  (select-window emigo-window)
  (set-window-dedicated-p (selected-window) t))

(defun emigo-create-window (buffer)
  "Display BUFFER in the dedicated Emigo window."
  (unless (bufferp buffer)
    (error "[Emigo] Invalid buffer provided to emigo-create-ai-window: %s" buffer))
  (setq emigo-buffer buffer)
  (unless (emigo-window-exist-p emigo-window)
    (setq emigo-window
          (display-buffer buffer ;; Display the specific buffer
                          `(display-buffer-in-side-window
                            (side . right)
                            (window-width . ,emigo-window-width)))))
  (select-window emigo-window)
  (set-window-buffer emigo-window emigo-buffer) ;; Ensure correct buffer is shown
  (set-window-dedicated-p (selected-window) t)
  ;; Start the enforcement timer
  (emigo--start-dedicated-window-timer))

(defvar emigo-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd "C-a") #'emigo-beginning-of-line)
    (define-key map (kbd "C-k") #'emigo-kill-line)
    (define-key map (kbd "C-m") #'emigo-send-prompt)
    (define-key map (kbd "C-c r") #'emigo-restart-process)
    (define-key map (kbd "C-c j") #'emigo-drop-file-from-context)
    (define-key map (kbd "C-c f") #'emigo-add-file-to-context)
    (define-key map (kbd "C-c l") #'emigo-ls-files-in-context)
    (define-key map (kbd "C-c H") #'emigo-clear-history)
    (define-key map (kbd "C-c h") #'emigo-show-history)
    (define-key map (kbd "C-c p") #'emigo-show-proc-buffer)
    (define-key map (kbd "C-c C-c") #'emigo-stop-call)
    (define-key map (kbd "S-<return>") #'emigo-send-newline)
    (define-key map (kbd "M-p") #'emigo-previous-prompt)
    (define-key map (kbd "M-n") #'emigo-next-prompt)
    (define-key map (kbd "<backspace>") #'emigo-backward-delete-char)
    (define-key map (kbd "DEL") #'emigo-backward-delete-char)
    map)
  "Keymap used by `emigo-mode'.")

(defvar-local emigo--prompt-history '("")
  "History of previous prompts in Emigo sessions.")

(defvar emigo-prompt-history-index 0
  "Current index in the prompt history.")

(defun emigo--cycle-prompt-history (direction)
  "Cycle through prompt history in DIRECTION (1 for older, -1 for newer).
Index 0 always corresponds to an empty prompt string."
  (when (> (length emigo--prompt-history) 1) ;; Only cycle if there's more than just ""
    (let ((hist-len (length emigo--prompt-history)))
      (setq emigo-prompt-history-index (+ emigo-prompt-history-index direction))
      ;; Wrap around logic
      (cond
       ((>= emigo-prompt-history-index hist-len) ;; Went past oldest, wrap to empty ""
        (setq emigo-prompt-history-index 0))
       ((< emigo-prompt-history-index 0) ;; Went past newest, wrap to oldest
        (setq emigo-prompt-history-index (1- hist-len))))

      ;; Go to the end, find the start of the current prompt text, delete it, and insert history
      (goto-char (point-max))
      (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
        (progn ;; Ensure both actions happen only if search succeeds
          (forward-char (length emigo-prompt-symbol))
          (delete-region (point) (point-max))))
      (insert (nth emigo-prompt-history-index emigo--prompt-history)))))

(defun emigo-previous-prompt ()
  "Navigate to previous prompt in history."
  (interactive)
  (let ((inhibit-read-only t))
    ;; Cycle towards older prompts (higher index in newest-first list)
    (emigo--cycle-prompt-history 1)))

(defun emigo-next-prompt ()
  "Navigate to next prompt in history."
  (interactive)
  (let ((inhibit-read-only t))
    ;; Cycle towards newer prompts (lower index in newest-first list)
    (emigo--cycle-prompt-history -1)))

(defun emigo--protect-prompt-line-p ()
  "Return non-nil if current line contains the prompt and should be protected."
  (save-excursion
    (beginning-of-line)
    (and (eq major-mode 'emigo-mode)
         (looking-at-p (concat "^" (regexp-quote emigo-prompt-symbol))))))

(define-derived-mode emigo-mode fundamental-mode "emigo"
  "Major mode for Emigo AI chat sessions.
\\{emigo-mode-map}"
  :group 'emigo
  (setq major-mode 'emigo-mode)
  (setq mode-name "emigo")
  (use-local-map emigo-mode-map)
  (setq-local emigo--prompt-history '(""))
  (setq-local emigo-prompt-history-index 0)
  (run-hooks 'emigo-mode-hook))

(defun emigo-send-newline ()
  "Insert a newline character at point."
  (interactive)
  (insert "\n"))

(defun emigo-beginning-of-line ()
  "Move to the beginning of the current line or the prompt position."
  (interactive)
  (if (emigo--protect-prompt-line-p)
      (progn
        (goto-char (line-beginning-position))
        (forward-char (length emigo-prompt-symbol)))
    (goto-char (line-beginning-position))))

(defun emigo-backward-delete-char ()
  "Delete the character before point, unless at the prompt boundary.
This is similar to `backward-delete-char' but protects the prompt line."
  (interactive)
  (let ((prompt-start (save-excursion
                        (goto-char (line-beginning-position))
                        (when (looking-at (regexp-quote emigo-prompt-symbol))
                          (point)))))
    (if (and prompt-start
             (<= (point) (+ prompt-start (length emigo-prompt-symbol))))
        (ding)
      (backward-delete-char 1))))

(defun emigo-kill-line ()
  "Kill the line in Emigo buffer with special handling for prompt lines.
If on a prompt line:
- Fails if point is within the prompt string
- Kills from point to end of line if after prompt
- Kills entire line if at end of line after prompt"
  (interactive)
  (when (emigo--protect-prompt-line-p)
    (let* ((line-start (line-beginning-position)))
      (if (< (point) (+ line-start (length emigo-prompt-symbol)))
          (ding)
        ;; We're after the prompt
        (let ((inhibit-read-only t))
          (if (eolp)
              ;; At end of line - kill whole line including newline
              (kill-region (+ line-start (length emigo-prompt-symbol)) (line-end-position))
            ;; Not at end - kill from point to end
            (kill-region (point) (line-end-position))))))))

(defun emigo-send-prompt ()
  "Send the current prompt to the AI planner."
  (interactive)
  (setq-local emigo--llm-output "")
  (let ((prompt (save-excursion
                  (goto-char (point-max))
                  (search-backward-regexp (concat "^" emigo-prompt-symbol) nil t)
                  (forward-char (length emigo-prompt-symbol))
                  (string-trim (buffer-substring-no-properties (point) (point-max))))))
    (if (string-empty-p prompt)
        (ding)
      ;; Add prompt to history
      (setcdr emigo--prompt-history (cons prompt (cdr emigo--prompt-history)))
      (setq emigo-prompt-history-index 0)

      ;; Clear the prompt from the buffer and display user prompt
      (let ((inhibit-read-only t))
        (goto-char (point-max))
        ;; The prompt is always inserted with two newlines before it.
        (when (search-backward-regexp (concat "\n\n" (regexp-quote emigo-prompt-symbol)) nil t)
          (delete-region (match-beginning 0) (point-max)))
        (goto-char (point-max))
        (insert (propertize (format "\n\nUser: %s" prompt) 'face 'font-lock-keyword-face)))

      ;; Send to planner
      (emigo-rpc-notify "start_planner" emigo-session-path prompt)
      (emigo--reinsert-prompt emigo-session-path))))


(defun emigo-lock-region (beg end)
  "Super-lock the region from BEG to END."
  (interactive "r")
  (put-text-property beg end 'read-only t)
  (let ((overlay (make-overlay beg end)))
    (overlay-put overlay 'modification-hooks (list (lambda (&rest args))))
    (overlay-put overlay 'front-sticky t)
    (overlay-put overlay 'rear-nonsticky nil)))

(defun emigo--advice-delete-other-windows (orig-fun &rest args)
  "Around advice for `delete-other-windows'.
Prevent deleting the dedicated Emigo window."
  (if (and (emigo-window-exist-p emigo-window)
           (not (eq (selected-window) emigo-window)))
      ;; If Emigo window exists and is not the selected one,
      ;; delete all other non-dedicated windows except the current one.
      (let ((current-window (selected-window)))
        (dolist (win (window-list))
          (when (and (window-live-p win)
                     (not (eq current-window win))
                     (not (eq emigo-window win)) ; Don't delete emigo window
                     (not (window-dedicated-p win))) ; Don't delete other dedicated windows
            (delete-window win)))
        nil) ; Indicate deletion happened
    ;; Otherwise (Emigo window doesn't exist or is selected), run original.
    (apply orig-fun args)))

(defun emigo--advice-other-window (orig-fun &rest args)
  "Around advice for `other-window'.
Skip the dedicated Emigo window when cycling."
  (let ((target-window (apply orig-fun args))) ; Call original first
    (if (and (emigo-window-exist-p emigo-window)
             (eq target-window emigo-window)
             ;; Check if we are trying to move *away* from emigo-window
             ;; or if the original call landed us there unintentionally.
             ;; This logic might need refinement depending on exact desired behavior.
             (not (eq (selected-window) emigo-window)))
        ;; If the original call selected the Emigo window, and it wasn't
        ;; the starting window, call other-window again with the same args
        ;; to skip over it.
        (apply orig-fun args)
      ;; Otherwise, return the window selected by the original call.
      target-window)))

(defun emigo--filter-return-other-window (window)
  "Filter return value of `other-window' to skip Emigo window."
  (if (and (emigo-window-exist-p emigo-window)
           (eq window emigo-window))
      ;; If the returned window is the emigo window, try again
      ;; This assumes the original `other-window` was called with count=1
      ;; A more robust solution would need access to the original args.
      (other-window 1)
    window))

;; --- End Window Management Advice ---


(defun emigo-add-file-to-context ()
  "Interactively add a file to the current project's Emigo chat context."
  (interactive)
  (user-error "This feature is not implemented in the current backend."))

(defun emigo-drop-file-from-context ()
  "Remove a file from the current project's Emigo chat context."
  (interactive)
  (user-error "This feature is not implemented in the current backend."))

(defun emigo--file-written-externally (abs-path)
  "Inform Emacs that the file at ABS-PATH was modified externally.
If the file is visited in a buffer, offer to revert it."
  (let ((buffer (find-buffer-visiting abs-path)))
    (when (and buffer (buffer-modified-p buffer))
      ;; If buffer is modified, ask user before reverting
      (when (y-or-n-p (format "File %s changed on disk; revert buffer?" (buffer-name buffer)))
        (with-current-buffer buffer
          (revert-buffer :ignore-auto :noconfirm))))
    (when (and buffer (not (buffer-modified-p buffer)))
      ;; If buffer is not modified, revert automatically
      (with-current-buffer buffer
        (revert-buffer :ignore-auto :noconfirm)))))

(defun emigo--agent-finished (session-path)
  "Callback function when the agent finishes its interaction for SESSION-PATH."
  ;; TODO: Maybe update a mode-line indicator?
  (message "[Emigo] Agent finished for session: %s" session-path)
  nil)

(defun emigo--clear-local-buffer (session-path)
  "Clear the local Emacs buffer content for SESSION-PATH.
Preserves the prompt history for convenience."
  (let ((buffer (get-buffer (emigo-get-buffer-name t session-path))))
    (when buffer
      (with-current-buffer buffer
        ;; Save current prompt history before clearing
        (let ((saved-prompt-history emigo--prompt-history))
          ;; Clear local buffer output accumulator
          (setq-local emigo--llm-output "")
          ;; History is managed on the Python side, no local history to clear
          ;; Erase buffer content and reset prompt
          (let ((inhibit-read-only t))
            (erase-buffer)
            (insert (propertize (concat "\n\n" emigo-prompt-symbol) 'face font-lock-keyword-face))
            (goto-char (point-max)))
          ;; Restore the prompt history after clearing
          (setq-local emigo--prompt-history saved-prompt-history)
          (message "Local buffer cleared for session: %s" session-path))))))

(defun emigo-clear-history ()
  "Clear the chat history for the current Emigo session."
  (interactive)
  (let ((session-path (emigo--ensure-session-path emigo-session-path)))
    (emigo-rpc-notify "clear_history" session-path)
    (emigo--clear-local-buffer session-path)
    (message "Emigo history cleared for %s" session-path)))

(defun emigo-show-history ()
  "Display the chat history for the current Emigo session in a new Org buffer."
  (interactive)
  (let* ((session-path (emigo--ensure-session-path emigo-session-path))
         (history (emigo-rpc-call "get_history" session-path))
         (history-buffer (get-buffer-create (format "*Emigo History: %s*" (file-name-nondirectory session-path)))))
    (with-current-buffer history-buffer
      (org-mode)
      (erase-buffer)
      (dolist (message history)
        (let ((role (cdr (assoc 'role message)))
              (content (cdr (assoc 'content message)))
              (tool-calls (cdr (assoc 'tool_calls message))))
          (insert (format "* %s\n" (upcase role)))
          (when (stringp content)
            (insert (format "#+begin_src text\n%s\n#+end_src\n\n" content)))
          (when (listp tool-calls)
            (dolist (tool-call tool-calls)
              (let* ((function-info (cdr (assoc 'function tool-call)))
                     (name (cdr (assoc 'name function-info)))
                     (args (cdr (assoc 'arguments function-info))))
                (insert (format "** TOOL CALL: %s\n" name))
                (insert (format "#+begin_src json\n%s\n#+end_src\n\n"
                                (json-encode-string args))))))
          (when (and (equal role "tool") (cdr (assoc 'content message)))
             (insert (format "#+begin_src json\n%s\n#+end_src\n\n"
                             (cdr (assoc 'content message)))))))
      (goto-char (point-min)))
    (display-buffer history-buffer)))

(defun emigo-send-revised-history ()
  "Parse the current history buffer and send it to start a new interaction."
  (interactive)
  (user-error "This feature is not implemented in the current backend."))

(defun emigo-stop-call ()
  "Cancel the currently running LLM interaction for this session."
  (interactive)
  (user-error "This feature is not implemented in the current backend."))

(defun emigo-ls-files-in-context ()
  "List the files currently in the Emigo chat context for the current project."
  (interactive)
  (user-error "This feature is not implemented in the current backend."))

(defun emigo-show-proc-buffer ()
  "Open and switch to the Emigo process buffer."
  (interactive)
  (let ((buf (get-buffer "*Emigo Process*")))
    (if buf
        (switch-to-buffer-other-window buf)
      (message "No Emigo process buffer found"))))

(provide 'emigo)
;;; emigo.el ends here
