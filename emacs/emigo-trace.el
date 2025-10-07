;;; emigo-trace.el --- Semantic trace log for Emigo -*- lexical-binding: t; -*-

(defvar emigo-trace-buffer-name "*Emigo Trace*"
  "The name of the buffer used for Emigo's trace log.")

(defun emigo-trace-add-log (intent-summary outcome)
  "Add a log entry to the Emigo trace buffer.
INTENT-SUMMARY is a human-readable string of the intent.
OUTCOME is a plist with :status ('success or 'error) and :message string."
  (with-current-buffer (get-buffer-create emigo-trace-buffer-name)
    (let ((inhibit-read-only t)
          (status (plist-get outcome :status))
          (message (plist-get outcome :message)))
      (goto-char (point-max))
      (insert (format-time-string "[%Y-%m-%d %H:%M:%S] "))
      (insert (format "Intent: %s\n" intent-summary))
      (insert (format "Status: %s\n" (upcase (symbol-name status))))
      (insert (format "Result: %s\n\n" message)))))

;;;###autoload
(defun emigo-trace-open ()
  "Display the Emigo trace buffer."
  (interactive)
  (display-buffer emigo-trace-buffer-name))

(provide 'emigo-trace)

;;; emigo-trace.el ends here
