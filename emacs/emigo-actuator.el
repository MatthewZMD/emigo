;;; emigo-actuator.el --- Execute Emigo intents -*- lexical-binding: t; -*-

(require 'emigo-trace)
(require 'thingatpt)


(defun emigo-actuator--find-target-buffer (selector)
  "Find and return the buffer specified by SELECTOR.
Switches to the buffer if found. Returns nil if not found."
  (let ((file-path (plist-get selector :file_path))
        (buffer-name (plist-get selector :buffer_name)))
    (cond
     (file-path (find-file-noselect file-path) (get-file-buffer file-path))
     (buffer-name (get-buffer buffer-name))
     (t (current-buffer)))))

(defun emigo-actuator--execute-add-keybinding (intent)
  "Execute the AddKeybinding intent."
  (let* ((selector (plist-get intent :target_selector))
         (params (plist-get intent :params))
         (key (plist-get params :key))
         (command-name (plist-get params :command_name))
         (command (intern command-name))
         (buffer (emigo-actuator--find-target-buffer selector))
         (form `(global-set-key (kbd ,key) #',command)))
    (unless buffer
      (error "Target buffer/file not found for AddKeybinding"))
    (unless (commandp command)
      (error "Command '%s' is not a valid, defined command" command-name))

    ;; First, make the keybinding live in the current session.
    (eval form)

    ;; Second, persist it to the target file.
    (with-current-buffer buffer
      (goto-char (point-max))
      (unless (bolp) (insert "\n\n"))
      (insert (format ";; Added by Emigo\n%s\n" (prin1-to-string form)))
      (save-buffer))
    (format "Key '%s' bound to '%s' and saved to %s" key command (buffer-name buffer))))

(defun emigo-actuator--execute-replace-sexp (intent)
  "Execute the ReplaceSexp intent."
  (let* ((selector (plist-get intent :target_selector))
         (params (plist-get intent :params))
         (new-sexp (plist-get params :new_sexp_string))
         (buffer (emigo-actuator--find-target-buffer selector))
         (defun-name (plist-get selector :defun_name)))
    (unless buffer
      (error "Target buffer/file not found for ReplaceSexp"))
    (unless new-sexp
      (error "Missing 'new_sexp_string' parameter for ReplaceSexp"))

    (with-current-buffer buffer
      (let* ((bounds
              (if defun-name
                  (progn
                    (goto-char (point-min))
                    (when (search-forward (format "(defun %s" defun-name) nil t)
                      (goto-char (match-beginning 0))
                      (cons (point) (progn (forward-sexp) (point)))))
                ;; else, use sexp at point
                (bounds-of-thing-at-point 'sexp)))
             (start-pos (car bounds))
             (end-pos (cdr bounds)))

        (unless start-pos
          (error "Could not find target S-expression for ReplaceSexp"))

        (delete-region start-pos end-pos)
        (insert new-sexp)
        ;; Indent the newly inserted region
        (let ((indent-start start-pos)
              (indent-end (point)))
          (indent-region indent-start indent-end))
        (save-buffer)
        (format "Replaced S-expression in %s" (buffer-name buffer))))))

(defun emigo-execute-intent (intent)
  "Execute an INTENT plist received from the agent.
This is the main entry point for the actuator."
  (let* ((intent-id (plist-get intent :intent_id))
         (intent-type (intern (plist-get intent :type)))
         (summary (plist-get intent :summary "No summary provided."))
         (outcome nil))
    (condition-case-unless-debug err
        (let ((result-message
               (pcase intent-type
                 ('AddKeybinding (emigo-actuator--execute-add-keybinding intent))
                 ('ReplaceSexp (emigo-actuator--execute-replace-sexp intent))
                 (_ (error "Intent type '%s' not implemented in actuator" intent-type)))))
          (setq outcome `(:status success :message ,result-message)))
      (error
       (setq outcome `(:status error :message ,(error-message-string err)))))
    (emigo-trace-add-log summary outcome)
    outcome))

(defun emigo-undo-intent (intent-id)
  "Undo a previously executed intent by ID."
  (interactive)
  ;; The journal is now managed by the Python agent.
  ;; This function is a placeholder for a future implementation
  ;; that would query the agent for the inverse intent.
  (error "Undo not yet implemented."))

;; --- State and Code Reading Functions ---

(defun emigo-read-sexps (selector)
  "Read S-expression at point or for a defun and return as string.
SELECTOR is a plist with :defun_name, etc."
  (let* ((buffer (emigo-actuator--find-target-buffer selector))
         (defun-name (plist-get selector :defun_name)))
    (unless buffer
      (error "Target buffer not found for read-sexps"))
    (with-current-buffer buffer
      (if defun-name
          (progn
            (goto-char (point-min))
            (when (search-forward (format "(defun %s" defun-name) nil t)
              (goto-char (match-beginning 0))
              (prin1-to-string (thing-at-point 'sexp))))
        ;; else, just grab sexp at point
        (prin1-to-string (thing-at-point 'sexp))))))

(defun emigo-list-buffers ()
  "Return a list of all buffer names."
  (mapcar #'buffer-name (buffer-list)))

(defun emigo-buffer-metadata (buffer-name)
  "Return a plist of metadata for BUFFER-NAME."
  (when-let ((buf (get-buffer buffer-name)))
    (with-current-buffer buf
      `(:file ,(buffer-file-name)
        :mode ,major-mode
        :size ,(buffer-size)
        :modified ,(buffer-modified-p)))))

(defun emigo-current-mode ()
  "Return the major-mode of the current buffer."
  (symbol-name major-mode))

(provide 'emigo-actuator)

;;; emigo-actuator.el ends here
