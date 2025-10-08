;;; emigo-rpc.el --- RPC transport layer -*- lexical-binding: t; -*-

(require 'emigo-epc)
(require 'json)

(declare-function emigo-get-buffer-name "emigo")
(declare-function emigo-lock-region "emigo")

(defvar emigo--process nil
  "The Emigo agent process.")
(defvar emigo--connection nil
  "The EPC connection TO the Emigo agent (Emacs as client).")
(defvar emigo--rpc-server-manager nil
  "The EPC manager for the server that Python connects TO (Emacs as server).")

(defun emigo--first-start (port)
  "Connect to the python server on PORT and create a manager for the connection."
  (unless emigo--connection
    (let* ((conn (emigo-epc-connect "localhost" port))
           ;; Create a manager object to wrap the connection, which is
           ;; required by emigo-epc-call-sync and emigo-epc-manager-send.
           (manager (make-emigo-epc-manager :port port :connection conn)))
      (emigo-epc-init-epc-layer manager)
      (setq emigo--connection manager)
      (message "Emigo connected on port %d." port))))

(defvar emigo-rpc-connect-hook nil
  "Hook run after the agent connects. The EPC manager is passed as an argument.")

(defun emigo--eval-in-emacs-func (sexp-string)
  (eval (read sexp-string))
  ;; Return nil to avoid epc error `Got too many parameters in the reply'.
  nil)

(defun emigo--get-emacs-var-func (var-name)
  (let* ((var-symbol (intern var-name))
         (var-value (symbol-value var-symbol))
         ;; We need convert result of booleanp to string.
         ;; Otherwise, python-epc will convert all `nil' to [] at Python side.
         (var-is-bool (prin1-to-string (booleanp var-value))))
    (list var-value var-is-bool)))

(defun emigo--get-emacs-vars-func (&rest vars)
  (mapcar #'emigo--get-emacs-var-func vars))

(defun emigo--flush-buffer (session-path content &optional role)
  "Flush CONTENT to the Emigo buffer associated with SESSION-PATH.
ROLE indicates the type of content (e.g., 'user', 'llm', 'error')."
  (let ((buffer (get-buffer (emigo-get-buffer-name t session-path)))) ;; Find existing buffer
    (unless buffer
      (warn "[Emigo] Could not find buffer for session %s to flush content: %s" session-path content)
      (cl-return-from emigo--flush-buffer))

    (with-current-buffer buffer
      (save-excursion
        (let ((inhibit-read-only t)) ;; Allow modification
          ;; Go to the end of the buffer, just before the prompt.
          (goto-char (point-max))
          (when (search-backward-regexp (concat "\n\n" (regexp-quote emigo-prompt-symbol)) nil t)
            (goto-char (match-beginning 0)))

          ;; --- Insert new content based on role ---
          (when content
            (cond
             ((equal role "user")
              (insert (propertize content 'face font-lock-keyword-face)))

             ((equal role "llm")
              (setq-local emigo--llm-output (concat (or emigo--llm-output "") content))
              ;; Remove leading whitespace from newlines to fix LLM formatting quirks.
              (insert (replace-regexp-in-string "\n[ \t]+" "\n" content)))

             ((equal role "error") ;; Handle error messages
              (insert (propertize content 'face 'error)))

             (t ;; Default case (e.g., if role is nil or unexpected)
              (insert content))))

          ;; --- Update read-only region ---
          (goto-char (point-max))
          (when (search-backward-regexp (concat "^" (regexp-quote emigo-prompt-symbol)) nil t)
            (forward-char (1- (length emigo-prompt-symbol)))
            (emigo-lock-region (point-min) (point))))))))

(defun emigo--reinsert-prompt (session-path)
  "Re-insert the prompt in the Emigo buffer for SESSION-PATH."
  (let ((buffer (get-buffer (emigo-get-buffer-name t session-path))))
    (when buffer
      (with-current-buffer buffer
        (let ((inhibit-read-only t))
          (goto-char (point-max))
          ;; Avoid double prompts
          (unless (looking-back (concat "\n" (regexp-quote emigo-prompt-symbol) " *$") nil)
            (insert (propertize (concat "\n\n" emigo-prompt-symbol) 'face font-lock-keyword-face))))))))

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

(defun emigo--register-ui-rpc-methods (manager)
  "Register Emigo UI-related RPC methods."
  (emigo-epc-define-method manager 'emigo--flush-buffer #'emigo--flush-buffer "Flush content to an Emigo buffer.")
  (emigo-epc-define-method manager 'emigo--reinsert-prompt #'emigo--reinsert-prompt "Re-insert the prompt in an emigo buffer."))

(defun emigo--rpc-on-connect (manager)
  "Callback when Python agent connects to our EPC server."
  (message "Emigo agent has connected to Emacs EPC server.")
  ;; Generic
  (emigo-epc-define-method manager 'emigo-message #'message "Show MESSAGE in the echo area.")
  (emigo-epc-define-method manager 'emigo--eval-in-emacs-func #'emigo--eval-in-emacs-func "Evaluate an elisp string.")
  (emigo-epc-define-method manager 'emigo--first-start #'emigo--first-start "Handle first start signal from Python.")
  (emigo-epc-define-method manager 'emigo--get-emacs-vars-func #'emigo--get-emacs-vars-func "Get values of multiple Emacs variables.")

  ;; Symbol/State Reading
  (emigo-epc-define-method manager 'emigo-apropos #'emigo-apropos "Search for symbols matching REGEXP.")
  (emigo-epc-define-method manager 'emacs_symbol_doc (lambda (s) (when-let ((sym (intern-soft s))) (documentation sym))) "Return the full docstring for a SYMBOL name.")
  (emigo-epc-define-method manager 'emacs_keybindings (lambda (s) (when-let ((sym (intern-soft s))) (let ((raw-keys (where-is-internal sym nil nil t))) (mapcar #'key-description raw-keys)))) "Return a list of keybindings for a SYMBOL name.")
  (emigo-epc-define-method manager 'emigo-list-buffers #'emigo-list-buffers "Return a list of buffer names.")
  (emigo-epc-define-method manager 'emigo-buffer-metadata #'emigo-buffer-metadata "Return metadata for a buffer.")
  (emigo-epc-define-method manager 'emigo-current-mode #'emigo-current-mode "Return the current major mode.")

  ;; Register UI methods now that they are part of this file.
  (emigo--register-ui-rpc-methods manager)

  (run-hook-with-args 'emigo-rpc-connect-hook manager))

(defun emigo--rpc-start-server ()
  "Start the Emacs-side EPC server for the agent to connect to.
Return the port number."
  (unless (and emigo--rpc-server-manager (process-live-p emigo--rpc-server-manager))
    (setq emigo--rpc-server-manager (emigo-epc-server-start #'emigo--rpc-on-connect)))
  (unless (and emigo--rpc-server-manager (process-live-p emigo--rpc-server-manager))
    (error "[Emigo] Failed to start Emacs EPC server."))
  (process-contact emigo--rpc-server-manager :service))

(defun emigo--rpc-process-filter (proc string)
  "Filter for the agent process output."
  (with-current-buffer (process-buffer proc)
    (insert string)))

(defun emigo--rpc-process-sentinel (proc msg)
  "Sentinel for the agent process."
  (setq emigo--process nil)
  (setq emigo--connection nil)
  (message "Emigo agent process died: %s" msg))

(defun emigo-rpc-connect ()
  "Start the Python agent process and connect to it."
  (when emigo--process
    (message "Emigo agent process is already running."))
  (let* ((emacs-server-port (emigo--rpc-start-server))
         (root-dir (file-name-directory (file-name-directory (locate-library "emigo-rpc.el"))))
         (proc-buffer (get-buffer-create "*Emigo Process*"))
         ;; Run python from the project root so module imports work.
         (default-directory root-dir))
    (setq emigo--process
          (start-process "emigo-agent"
                         proc-buffer
                         "python3"
                         "-m" "python.server"
                         (number-to-string emacs-server-port)))
    (set-process-filter emigo--process #'emigo--rpc-process-filter)
    (set-process-sentinel emigo--process #'emigo--rpc-process-sentinel)))

(defun emigo-rpc-call (method &rest params)
  "Send a synchronous RPC call to the Python agent."
  (unless emigo--connection
    (user-error "Emigo agent not connected."))
  (let* ((request `((id . ,(emigo-epc-uid))
                    (type . "call")
                    (method . ,method)
                    (params . ,(apply #'list params))))
         ;; The remote method `handle_request` takes one argument, the JSON string.
         (response (emigo-epc-call-sync emigo--connection 'handle_request (json-serialize request))))
    ;; The response is a plist converted from the Python dictionary by EPC.
    (if (plist-get response :ok)
        (plist-get response :result)
      (let ((error (plist-get response :error)))
        (user-error "Emigo RPC Error: %s (code: %s)"
                    (plist-get error :message)
                    (plist-get error :code))))))

(defun emigo-rpc-notify (method &rest params)
  "Send an asynchronous RPC notification to the Python agent."
  (when emigo--connection
    (let* ((request-json (json-serialize
                          `(:id ,(emigo-epc-uid)
                            :type "call"
                            :method ,method
                            :params ,(vconcat params))))  ; vconcat creates a vector from the list
           (args-list (list request-json)))
      (emigo-epc-call-deferred emigo--connection 'handle_request args-list))))

(provide 'emigo-rpc)

;;; emigo-rpc.el ends here
