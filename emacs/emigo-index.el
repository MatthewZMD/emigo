;;; emigo-index.el --- Symbol indexer and watcher -*- lexical-binding: t; -*-

(require 'json)
(require 'subr-x) ; for string-prefix-p

(defvar emigo-cache-dir (expand-file-name ".cache/emigo/" user-emacs-directory)
  "Directory to store Emigo cache files.")

(defun emigo-index--get-symbol-kind (symbol)
  "Return the kind of SYMBOL as a string."
  (cond
   ((commandp symbol) "command")
   ((macrop symbol) "macro")
   ((fboundp symbol) "function")
   ((facep symbol) "face")
   ((custom-variable-p symbol) "variable") ; more specific than just boundp
   ((boundp symbol) "variable")
   (t "symbol")))

(defun emigo-index--get-prefix (symbol-name)
  "Extract a prefix from SYMBOL-NAME."
  (when-let ((match (string-match "^\\([a-zA-Z0-9-]+\\)-" symbol-name)))
    (match-string 1 symbol-name)))

(defun emigo-index--get-source-info (symbol)
  "Return a plist with :file and :line for SYMBOL."
  (let ((file (ignore-errors (find-lisp-object-file symbol))))
    (when file
      (let ((pos (ignore-errors (find-lisp-object-file symbol file))))
        (list :file file
              :line (when pos
                      (with-current-buffer (find-file-noselect file)
                        (line-number-at-pos pos))))))))

(defun emigo-index--get-keybindings (symbol)
  "Return a list of keybindings for SYMBOL."
  (let ((raw-keys (where-is-internal symbol nil nil t)))
    (mapcar #'key-description raw-keys)))

;;;###autoload
(defun emigo-index-build ()
  "Build the symbol index and send it to the Python agent."
  (interactive)
  (message "Building Emigo symbol index...")
  (let ((symbols-batch '())
        (count 0)
        (batch-size 100)
        (features-by-file (make-hash-table :test 'equal)))
    (dolist (item load-history)
      (when (stringp (car item))
        (puthash (car item) (cdr item) features-by-file)))
    (mapatoms
     (lambda (symbol)
       (let* ((source-info (emigo-index--get-source-info symbol))
              (file (plist-get source-info :file)))
         (when (and file
                    (or (string-prefix-p emigo-user-config-dir file)
                        (string-prefix-p emigo-packages-dir file)))
           (let* ((name (symbol-name symbol))
                  (file-features (gethash file features-by-file))
                  (provides (cdr (assq 'provide file-features)))
                  (requires (cdr (assq 'require file-features)))
                  (package-name (if provides (symbol-name (car provides)) (emigo-index--get-prefix name)))
                  (record `(:symbol ,name
                            :kind ,(emigo-index--get-symbol-kind symbol)
                            :package ,package-name
                            :prefix ,(emigo-index--get-prefix name)
                            :file ,file
                            :line ,(plist-get source-info :line)
                            :interactive ,(commandp symbol 't)
                            :args ,(when (fboundp symbol) (let ((arglist (ignore-errors (arglist symbol)))) (when (listp arglist) (mapcar #'symbol-name arglist))))
                            :doc_excerpt ,(when-let ((doc (documentation symbol)))
                                            (substring doc 0 (min 200 (length doc))))
                            :keybindings ,(emigo-index--get-keybindings symbol)
                            :provides ,(mapcar #'symbol-name provides)
                            :requires ,(mapcar #'symbol-name requires)
                            :last_modified ,(when-let ((attrs (file-attributes file)))
                                              (time-convert (file-attribute-modification-time attrs) 'integer)))))
             (push record symbols-batch)
             (setq count (1+ count))
             (when (= (length symbols-batch) batch-size)
               (emigo-rpc-notify "update_symbols_batch" symbols-batch)
               (setq symbols-batch '())))))))
    ;; Send any remaining symbols
    (when symbols-batch
      (emigo-rpc-notify "update_symbols_batch" symbols-batch))
    (message "Building Emigo symbol index... done. Sent %d symbols to agent." count)))

(defvar emigo-index--ignored-dirs-regex
  (concat "\\("
          (regexp-quote "/.git/") "\\|"
          (regexp-quote "/node_modules/") "\\|"
          (regexp-quote "/__pycache__/") "\\|"
          ;; Also ignore changes to our own cache dir
          (regexp-quote (file-name-as-directory emigo-cache-dir))
          "\\)")
  "Regex to ignore directories in file watcher.")

(defun emigo-index--watch-handler (event)
  "Handle file change notifications for indexing."
  ;; event is a list: (ACTION FILE-PATH &optional RENAMED-TO)
  (let ((action (car event))
        (file (cadr event)))
    (when (and (member action '(created changed renamed))
               (not (string-match-p emigo-index--ignored-dirs-regex file))
               (file-exists-p file)
               (not (file-directory-p file)))
      (message "Emigo: Detected change in %s, notifying agent." file)
      (let* ((attrs (file-attributes file))
             (mtime (time-convert (file-attribute-modification-time attrs) 'integer)))
        (emigo-rpc-notify "notify_file_changed" file mtime)))))

;;;###autoload
(defun emigo-index-watch ()
  "Start watching user config and packages directories for changes."
  (interactive)
  (message "Emigo: Starting file watchers for index.")
  (let ((dirs (list emigo-user-config-dir emigo-packages-dir)))
    (dolist (dir dirs)
      ;; The `change' event is recursive for directories.
      (file-notify-add-watch dir '(change) #'emigo-index--watch-handler))))

(provide 'emigo-index)

;;; emigo-index.el ends here
