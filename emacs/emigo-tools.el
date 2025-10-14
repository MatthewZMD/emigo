;;; emigo-tools.el --- Emacs functions exposed as tools to the LLM. -*- lexical-binding: t; -*-

(defun emigo--apropos-internal (regexp pred)
  "Return a list of symbols matching REGEXP filtered by PRED."
  (let (res)
    (mapatoms (lambda (s)
                (when (and (funcall pred s)
                           (or (string-match-p regexp (symbol-name s))
                               (and (documentation s) (string-match-p regexp (documentation s)))))
                  (push s res))))
    (nreverse res)))

(defun emigo--format-apropos-results (symbols)
  "Format a list of SYMBOLS into a plist for the LLM."
  (let (results)
    (dolist (s symbols)
      (let* ((kind (cond
                    ((commandp s) "command")
                    ((macrop s) "macro")
                    ((fboundp s) "function")
                    ((facep s) "face")
                    ((custom-variable-p s) "variable")
                    ((boundp s) "variable")
                    (t "symbol")))
             (doc (documentation s))
             (doc-excerpt (when doc (substring doc 0 (min 200 (length doc))))))
        (push `(:symbol ,(symbol-name s)
                :kind ,kind
                :doc_excerpt ,doc-excerpt)
              results)))
    (nreverse results)))

(defun emigo-apropos-command (regexp)
  "Search for commands matching REGEXP."
  (emigo--format-apropos-results (emigo--apropos-internal regexp #'commandp)))

(defun emigo-apropos-function (regexp)
  "Search for functions matching REGEXP."
  (emigo--format-apropos-results (emigo--apropos-internal regexp #'fboundp)))

(defun emigo-apropos-variable (regexp)
  "Search for variables matching REGEXP."
  (emigo--format-apropos-results (emigo--apropos-internal regexp #'boundp)))

(defun emigo-list-buffers ()
  "Return a list of all buffer names."
  (mapcar #'buffer-name (buffer-list)))

(defun emigo-buffer-metadata (buffer-name)
  "Return a plist of metadata for BUFFER-NAME."
  (let ((buf (get-buffer buffer-name)))
    (when buf
      (with-current-buffer buf
        `(:name ,(buffer-name)
          :file ,(buffer-file-name)
          :mode ,(symbol-name major-mode)
          :size ,(buffer-size)
          :modified ,(buffer-modified-p))))))

(defun emigo-symbol-doc (symbol-name)
  "Return the documentation for SYMBOL-NAME."
  (let ((symbol (intern-soft symbol-name)))
    (when symbol
      (documentation symbol))))

(defun emigo-keybindings (symbol-name)
  "Return a list of keybindings for the command SYMBOL-NAME."
  (let ((symbol (intern-soft symbol-name)))
    (when (and symbol (commandp symbol))
      (mapcar #'key-description (where-is-internal symbol)))))

(defun emigo-eval-elisp (code)
  "Evaluate CODE string as Emacs Lisp and return result as a string."
  (prin1-to-string (eval (read-from-string code))))

(defun emigo-symbol-source (symbol-name)
  "Return the source code for SYMBOL-NAME."
  (let ((symbol (intern-soft symbol-name)))
    (when (and symbol (fboundp symbol))
      (save-excursion
        (let ((buffer (find-function-get-buffer symbol)))
          (when buffer
            (with-current-buffer buffer
              (goto-char (find-function-search-for-symbol symbol))
              (let ((start (point)))
                (forward-sexp)
                (buffer-substring-no-properties start (point))))))))))

(defun emigo-symbol-value (symbol-name)
  "Return the value of a symbol as a string."
  (let ((symbol (intern-soft symbol-name)))
    (if (and symbol (boundp symbol))
        (prin1-to-string (symbol-value symbol))
      (format "Symbol '%s' is not bound." symbol-name))))

(defun emigo-visible-buffers-content ()
  "Return a list of plists with buffer name and visible content for each window.
Excludes Emigo buffers and the minibuffer."
  (let ((results '()))
    (dolist (window (window-list))
      (unless (or (minibuffer-window-active-p window)
                  (string-match-p "^\\*emigo:.*\\*$" (buffer-name (window-buffer window))))
        (with-current-buffer (window-buffer window)
          (let* ((start (window-start window))
                 (end (window-end window))
                 (content (buffer-substring-no-properties start end)))
            (push `(:buffer_name ,(buffer-name)
                    :content ,content)
                  results)))))
    (nreverse results)))

(provide 'emigo-tools)
;;; emigo-tools.el ends here
