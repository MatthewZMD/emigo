;;; emigo-tools.el --- Emacs functions exposed as tools to the LLM. -*- lexical-binding: t; -*-

(defun emigo-apropos (regexp)
  "Return a list of symbols and their documentation matching REGEXP.
This is a wrapper around `apropos-symbols' that returns structured data."
  (let* ((symbols (apropos-symbols (if (string-prefix-p "'" regexp) (substring regexp 1) regexp)))
         (results '()))
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

(defun emigo-current-mode ()
  "Return the major-mode of the current buffer."
  (symbol-name major-mode))

(provide 'emigo-tools)
;;; emigo-tools.el ends here
