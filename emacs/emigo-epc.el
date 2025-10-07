;;; epcs.el --- EPC Server              -*- lexical-binding: t -*-

;; Copyright (C) 2011,2012,2013  Masashi Sakurai

;; Author: Masashi Sakurai <m.sakurai at kiwanami.net>
;; Keywords: lisp

;; This program is free software; you can redistribute it and/or modify
;; it under the terms of the GNU General Public License as published by
;; the Free Software Foundation, either version 3 of the License, or
;; (at your option) any later version.

;; This program is distributed in the hope that it will be useful,
;; but WITHOUT ANY WARRANTY; without even the implied warranty of
;; MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
;; GNU General Public License for more details.

;; You should have received a copy of the GNU General Public License
;; along with this program.  If not, see <http://www.gnu.org/licenses/>.

;;; Commentary:

;;

;;; Code:

(require 'cl-lib)
(require 'subr-x)

;; deferred
(cl-defmacro emigo-deferred-chain (&rest elements)
  "Anaphoric function chain macro for deferred chains."
  (declare (debug (&rest form))
           (indent 0))
  `(let (it)
     ,@(cl-loop for i in elements
                collect
                `(setq it ,i))
     it))

;; Debug
(defvar emigo-deferred-debug nil
  "Debug output switch.")

(defvar emigo-deferred-debug-count 0
  "[internal] Debug output counter.")

(defun emigo-deferred-log (&rest args)
  "[internal] Debug log function."
  (when emigo-deferred-debug
    (with-current-buffer (get-buffer-create "*emigo-deferred-log*")
      (save-excursion
        (goto-char (point-max))
        (insert (format "%5i %s\n\n\n" emigo-deferred-debug-count (apply #'format args)))))
    (cl-incf emigo-deferred-debug-count)))

(defvar emigo-deferred-debug-on-signal nil
  "If non nil, the value `debug-on-signal' is substituted this
value in the `condition-case' form in deferred
implementations. Then, Emacs debugger can catch an error occurred
in the asynchronous tasks.")

(cl-defmacro emigo-deferred-condition-case (var protected-form &rest handlers)
  "[internal] Custom condition-case. See the comment for
`emigo-deferred-debug-on-signal'."
  (declare (debug condition-case)
           (indent 1))
  `(let ((debug-on-signal
          (or debug-on-signal emigo-deferred-debug-on-signal)))
     (condition-case ,var
         ,protected-form
       ,@handlers)))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Back end functions of deferred tasks

(defvar emigo-deferred-tick-time 0.001
  "Waiting time between asynchronous tasks (second).
The shorter waiting time increases the load of Emacs. The end
user can tune this parameter. However, applications should not
modify it because the applications run on various environments.")

(defvar emigo-deferred-queue nil
  "[internal] The execution queue of deferred objects.
See the functions `emigo-deferred-post-task' and `emigo-deferred-worker'.")

(defun emigo-deferred-post-task (d which &optional arg)
  "[internal] Add a deferred object to the execution queue
`emigo-deferred-queue' and schedule to execute.
D is a deferred object. WHICH is a symbol, `ok' or `ng'. ARG is
an argument value for execution of the deferred task."
  (let ((pack `(,d ,which . ,arg)))
    (push pack emigo-deferred-queue)
    (emigo-deferred-log "QUEUE-POST [%s]: %s" (length emigo-deferred-queue) pack)
    (run-at-time emigo-deferred-tick-time nil 'emigo-deferred-worker)
    d))

(defun emigo-deferred-worker ()
  "[internal] Consume a deferred task.
Mainly this function is called by timer asynchronously."
  (when emigo-deferred-queue
    (let* ((pack (car (last emigo-deferred-queue)))
           (d (car pack))
           (which (cadr pack))
           (arg (cddr pack)) value)
      (setq emigo-deferred-queue (nbutlast emigo-deferred-queue))
      (condition-case err
          (setq value (emigo-deferred-exec-task d which arg))
        (error
         (emigo-deferred-log "ERROR : %s" err)
         (message "deferred error : %s" err)))
      value)))

;; Struct: emigo-deferred-object
;;
;; callback    : a callback function (default `identity')
;; errorback   : an errorback function (default `emigo-deferred-resignal')
;; cancel      : a canceling function (default `emigo-deferred-default-cancel')
;; next        : a next chained deferred object (default nil)
;; status      : if 'ok or 'ng, this deferred has a result (error) value. (default nil)
;; value       : saved value (default nil)
;;
(cl-defstruct emigo-deferred-object
  (callback 'identity)
  (errorback 'emigo-deferred-resignal)
  (cancel 'emigo-deferred-default-cancel)
  next status value)

(defun emigo-deferred-resignal (err)
  "[internal] Safely resignal ERR as an Emacs condition.

If ERR is a cons (ERROR-SYMBOL . DATA) where ERROR-SYMBOL has an
`error-conditions' property, it is re-signaled unchanged. If ERR
is a string, it is signaled as a generic error using `error'.
Otherwise, ERR is formatted into a string as if by `print' before
raising with `error'."
  (cond ((and (listp err)
              (symbolp (car err))
              (get (car err) 'error-conditions))
         (signal (car err) (cdr err)))
        ((stringp err)
         (error "%s" err))
        (t
         (error "%S" err))))

(defun emigo-deferred-default-cancel (d)
  "[internal] Default canceling function."
  (emigo-deferred-log "CANCEL : %s" d)
  (setf (emigo-deferred-object-callback d) 'identity)
  (setf (emigo-deferred-object-errorback d) 'emigo-deferred-resignal)
  (setf (emigo-deferred-object-next d) nil)
  d)

(defun emigo-deferred-exec-task (d which &optional arg)
  "[internal] Executing deferred task. If the deferred object has
next deferred task or the return value is a deferred object, this
function adds the task to the execution queue.
D is a deferred object. WHICH is a symbol, `ok' or `ng'. ARG is
an argument value for execution of the deferred task."
  (emigo-deferred-log "EXEC : %s / %s / %s" d which arg)
  (when (null d) (error "emigo-deferred-exec-task was given a nil."))
  (let ((callback (if (eq which 'ok)
                      (emigo-deferred-object-callback d)
                    (emigo-deferred-object-errorback d)))
        (next-deferred (emigo-deferred-object-next d)))
    (cond
     (callback
      (emigo-deferred-condition-case err
                                     (let ((value (funcall callback arg)))
                                       (cond
                                        ((emigo-deferred-object-p value)
                                         (emigo-deferred-log "WAIT NEST : %s" value)
                                         (if next-deferred
                                             (emigo-deferred-set-next value next-deferred)
                                           value))
                                        (t
                                         (if next-deferred
                                             (emigo-deferred-post-task next-deferred 'ok value)
                                           (setf (emigo-deferred-object-status d) 'ok)
                                           (setf (emigo-deferred-object-value d) value)
                                           value))))
                                     (error
                                      (cond
                                       (next-deferred
                                        (emigo-deferred-post-task next-deferred 'ng err))
                                       (t
                                        (emigo-deferred-log "ERROR : %S" err)
                                        (message "deferred error : %S" err)
                                        (setf (emigo-deferred-object-status d) 'ng)
                                        (setf (emigo-deferred-object-value d) err)
                                        err)))))
     (t                                 ; <= (null callback)
      (cond
       (next-deferred
        (emigo-deferred-exec-task next-deferred which arg))
       ((eq which 'ok) arg)
       (t                               ; (eq which 'ng)
        (emigo-deferred-resignal arg)))))))

(defun emigo-deferred-set-next (prev next)
  "[internal] Connect deferred objects."
  (setf (emigo-deferred-object-next prev) next)
  (cond
   ((eq 'ok (emigo-deferred-object-status prev))
    (setf (emigo-deferred-object-status prev) nil)
    (let ((ret (emigo-deferred-exec-task
                next 'ok (emigo-deferred-object-value prev))))
      (if (emigo-deferred-object-p ret) ret
        next)))
   ((eq 'ng (emigo-deferred-object-status prev))
    (setf (emigo-deferred-object-status prev) nil)
    (let ((ret (emigo-deferred-exec-task next 'ng (emigo-deferred-object-value prev))))
      (if (emigo-deferred-object-p ret) ret
        next)))
   (t
    next)))

(defun emigo-deferred-new (&optional callback)
  "Create a deferred object."
  (if callback
      (make-emigo-deferred-object :callback callback)
    (make-emigo-deferred-object)))

(defun emigo-deferred-callback (d &optional arg)
  "Start deferred chain with a callback message."
  (emigo-deferred-exec-task d 'ok arg))

(defun emigo-deferred-errorback (d &optional arg)
  "Start deferred chain with an errorback message."
  (declare (indent 1))
  (emigo-deferred-exec-task d 'ng arg))

(defun emigo-deferred-callback-post (d &optional arg)
  "Add the deferred object to the execution queue."
  (declare (indent 1))
  (emigo-deferred-post-task d 'ok arg))

(defun emigo-deferred-next (&optional callback arg)
  "Create a deferred object and schedule executing. This function
is a short cut of following code:
 (emigo-deferred-callback-post (emigo-deferred-new callback))."
  (let ((d (if callback
               (make-emigo-deferred-object :callback callback)
             (make-emigo-deferred-object))))
    (emigo-deferred-callback-post d arg)
    d))

(defun emigo-deferred-nextc (d callback)
  "Create a deferred object with OK callback and connect it to the given deferred object."
  (declare (indent 1))
  (let ((nd (make-emigo-deferred-object :callback callback)))
    (emigo-deferred-set-next d nd)))

(defun emigo-deferred-error (d callback)
  "Create a deferred object with errorback and connect it to the given deferred object."
  (declare (indent 1))
  (let ((nd (make-emigo-deferred-object :errorback callback)))
    (emigo-deferred-set-next d nd)))

(defvar emigo-epc-debug nil)

(defun emigo-epc-log (&rest args)
  (when emigo-epc-debug
    (with-current-buffer (get-buffer-create "*emigo-epc-log*")
      (buffer-disable-undo)
      (goto-char (point-max))
      (insert (apply 'format args) "\n\n\n"))))

(defun emigo-epc-make-procbuf (name)
  "[internal] Make a process buffer."
  (let ((buf (get-buffer-create name)))
    (with-current-buffer buf
      (set (make-local-variable 'kill-buffer-query-functions) nil)
      (erase-buffer) (buffer-disable-undo))
    buf))

(defvar emigo-epc-uid 1)

(defun emigo-epc-uid ()
  (cl-incf emigo-epc-uid))

(defvar emigo-epc-accept-process-timeout 150
  "Asynchronous timeout time. (msec)")

(put 'epc-error 'error-conditions '(error epc-error))
(put 'epc-error 'error-message "EPC Error")

(cl-defstruct emigo-epc-connection
  "Set of information for network connection and event handling.

name    : Connection name. This name is used for process and buffer names.
process : Connection process object.
buffer  : Working buffer for the incoming data.
channel : Event channels for incoming messages."
  name process buffer channel)

(defun emigo-epc-connect (host port)
  "[internal] Connect the server, initialize the process and
return emigo-epc-connection object."
  (emigo-epc-log ">> Connection start: %s:%s" host port)
  (let* ((connection-id (emigo-epc-uid))
         (connection-name (format "emigo-epc con %s" connection-id))
         (connection-buf (emigo-epc-make-procbuf (format "*%s*" connection-name)))
         (connection-process
          (open-network-stream connection-name connection-buf host port))
         (channel (list connection-name nil))
         (connection (make-emigo-epc-connection
                      :name connection-name
                      :process connection-process
                      :buffer connection-buf
                      :channel channel)))
    (emigo-epc-log ">> Connection establish")
    (set-process-coding-system  connection-process 'binary 'binary)
    (set-process-filter connection-process
                        (lambda (p m)
                          (emigo-epc-process-filter connection p m)))
    (set-process-sentinel connection-process
                          (lambda (p e)
                            (emigo-epc-process-sentinel connection p e)))
    (set-process-query-on-exit-flag connection-process nil)
    connection))

(defun emigo-epc-process-sentinel (connection process msg)
  (emigo-epc-log "!! Process Sentinel [%s] : %S : %S"
                 (emigo-epc-connection-name connection) process msg)
  (emigo-epc-disconnect connection))

(defun emigo-epc-net-send (connection sexp)
  (let* ((msg (encode-coding-string
               (concat (emigo-epc-prin1-to-string sexp) "\n") 'utf-8-unix))
         (string (concat (format "%06x" (length msg)) msg))
         (proc (emigo-epc-connection-process connection)))
    (emigo-epc-log ">> SEND : [%S]" string)
    (process-send-string proc string)))

(defun emigo-epc-disconnect (connection)
  (let ((process (emigo-epc-connection-process connection))
        (buf (emigo-epc-connection-buffer connection))
        (name (emigo-epc-connection-name connection)))
    (emigo-epc-log "!! Disconnect [%s]" name)
    (when process
      (set-process-sentinel process nil)
      (delete-process process)
      (when (get-buffer buf) (kill-buffer buf)))
    (emigo-epc-log "!! Disconnected finished [%s]" name)))

(defun emigo-epc-process-filter (connection process message)
  (emigo-epc-log "INCOMING: [%s] [%S]" (emigo-epc-connection-name connection) message)
  (with-current-buffer (emigo-epc-connection-buffer connection)
    (goto-char (point-max))
    (insert message)
    (emigo-epc-process-available-input connection process)))

(defun emigo-epc-signal-connect (channel event-sym &optional callback)
  "Append an observer for EVENT-SYM of CHANNEL and return a deferred object.
If EVENT-SYM is `t', the observer receives all signals of the channel.
If CALLBACK function is given, the deferred object executes the
CALLBACK function asynchronously. One can connect subsequent
tasks to the returned deferred object."
  (let ((d (if callback
               (emigo-deferred-new callback)
             (emigo-deferred-new))))
    (push (cons event-sym d)
          (cddr channel))
    d))

(defun emigo-epc-signal-send (channel event-sym &rest args)
  "Send a signal to CHANNEL. If ARGS values are given,
observers can get the values by following code:

  (lambda (event)
    (destructuring-bind
     (event-sym (args))
     event ... ))
"
  (let ((observers (cddr channel))
        (event (list event-sym args)))
    (cl-loop for i in observers
             for name = (car i)
             for d = (cdr i)
             if (or (eq event-sym name) (eq t name))
             do (emigo-deferred-callback-post d event))))

(defun emigo-epc-process-available-input (connection process)
  "Process all complete messages that have arrived from Lisp."
  (with-current-buffer (process-buffer process)
    (while (emigo-epc-net-have-input-p)
      (let ((event (emigo-epc-net-read-or-lose process))
            (ok nil))
        (emigo-epc-log "<< RECV [%S]" event)
        (unwind-protect
            (condition-case err
                (progn
                  (apply 'emigo-epc-signal-send
                         (cons (emigo-epc-connection-channel connection) event))
                  (setq ok t))
              ('error (emigo-epc-log "MsgError: %S / <= %S" err event)))
          (unless ok
            (emigo-epc-process-available-input connection process)))))))

(defun emigo-epc-net-have-input-p ()
  "Return true if a complete message is available."
  (goto-char (point-min))
  (and (>= (buffer-size) 6)
       (>= (- (buffer-size) 6) (emigo-epc-net-decode-length))))

(defun emigo-epc-net-read-or-lose (_process)
  (condition-case error
      (emigo-epc-net-read)
    (error
     (debug 'error error)
     (error "net-read error: %S" error))))

(defun emigo-epc-net-read ()
  "Read a message from the network buffer."
  (goto-char (point-min))
  (let* ((length (emigo-epc-net-decode-length))
         (start (+ 6 (point)))
         (end (+ start length))
         _content)
    (cl-assert (cl-plusp length))
    (prog1 (save-restriction
             (narrow-to-region start end)
             (read (decode-coding-string
                    (buffer-string) 'utf-8-unix)))
      (delete-region (point-min) end))))

(defun emigo-epc-net-decode-length ()
  "Read a 24-bit hex-encoded integer from buffer."
  (string-to-number (buffer-substring-no-properties (point) (+ (point) 6)) 16))

(defun emigo-epc-prin1-to-string (sexp)
  "Like `prin1-to-string' but don't octal-escape non-ascii characters.
This is more compatible with the CL reader."
  (with-temp-buffer
    (let (print-escape-nonascii
          print-escape-newlines
          print-length
          print-level)
      (prin1 sexp (current-buffer))
      (buffer-string))))

(cl-defstruct emigo-epc-manager
  "Root object that holds all information related to an EPC activity.

`emigo-epc-start-epc' returns this object.

title          : instance name for displaying on the `emigo-epc-controller' UI
server-process : process object for the peer
commands       : a list of (prog . args)
port           : port number
connection     : emigo-epc-connection instance
methods        : alist of method (name . function)
sessions       : alist of session (id . deferred)
exit-hook      : functions for after shutdown EPC connection"
  title server-process commands port connection methods sessions exit-hooks)

(cl-defstruct emigo-epc-method
  "Object to hold serving method information.

name       : method name (symbol)   ex: 'test
task       : method function (function with one argument)
arg-specs  : arg-specs (one string) ex: \"(A B C D)\"
docstring  : docstring (one string) ex: \"A test function. Return sum of A,B,C and D\"
"
  name task docstring arg-specs)

(defvar emigo-epc-live-connections nil
  "[internal] A list of `emigo-epc-manager' objects.
those objects currently connect to the epc peer.
This variable is for debug purpose.")

(defun emigo-epc-server-process-name (uid)
  (format "emigo-epc-server:%s" uid))

(defun emigo-epc-server-buffer-name (uid)
  (format " *%s*" (emigo-epc-server-process-name uid)))

(defun emigo-epc-stop-epc (mngr)
  "Disconnect the connection for the server."
  (let* ((proc (emigo-epc-manager-server-process mngr))
         (buf (and proc (process-buffer proc))))
    (emigo-epc-disconnect (emigo-epc-manager-connection mngr))
    (when proc
      (accept-process-output proc 0 emigo-epc-accept-process-timeout t))
    (when (and proc (equal 'run (process-status proc)))
      (kill-process proc))
    (when buf  (kill-buffer buf))
    (setq emigo-epc-live-connections (delete mngr emigo-epc-live-connections))
    ))

(defun emigo-epc-args (args)
  "[internal] If ARGS is an atom, return it. If list, return the cadr of it."
  (cond
   ((atom args) args)
   (t (cadr args))))

(defun emigo-epc-init-epc-layer (mngr)
  "[internal] Connect to the server program and return an emigo-epc-connection instance."
  (let* ((mngr mngr)
         (conn (emigo-epc-manager-connection mngr))
         (channel (emigo-epc-connection-channel conn)))
    ;; dispatch incoming messages with the lexical scope
    (cl-loop for (method . body) in
             `((call
                . (lambda (args)
                    (emigo-epc-log "SIG CALL: %S" args)
                    (apply 'emigo-epc-handler-called-method ,mngr (emigo-epc-args args))))
               (return
                . (lambda (args)
                    (emigo-epc-log "SIG RET: %S" args)
                    (apply 'emigo-epc-handler-return ,mngr (emigo-epc-args args))))
               (return-error
                . (lambda (args)
                    (emigo-epc-log "SIG RET-ERROR: %S" args)
                    (apply 'emigo-epc-handler-return-error ,mngr (emigo-epc-args args))))
               (epc-error
                . (lambda (args)
                    (emigo-epc-log "SIG EPC-ERROR: %S" args)
                    (apply 'emigo-epc-handler-epc-error ,mngr (emigo-epc-args args))))
               (methods
                . (lambda (args)
                    (emigo-epc-log "SIG METHODS: %S" args)
                    (emigo-epc-handler-methods ,mngr (caadr args))))
               ) do
             (emigo-epc-signal-connect channel method body))
    (push mngr emigo-epc-live-connections)
    mngr))

(defun emigo-epc-manager-send (mngr method &rest messages)
  "[internal] low-level message sending."
  (let* ((conn (emigo-epc-manager-connection mngr)))
    (emigo-epc-net-send conn (cons method messages))))

(defun emigo-epc-manager-get-method (mngr method-name)
  "[internal] Return a method object. If not found, return nil."
  (cl-loop for i in (emigo-epc-manager-methods mngr)
           if (eq method-name (emigo-epc-method-name i))
           do (cl-return i)))

(defun emigo-epc-handler-methods (mngr uid)
  "[internal] Return a list of information for registered methods."
  (let ((info
         (cl-loop for i in (emigo-epc-manager-methods mngr)
                  collect
                  (list
                   (emigo-epc-method-name i)
                   (or (emigo-epc-method-arg-specs i) "")
                   (or (emigo-epc-method-docstring i) "")))))
    (emigo-epc-manager-send mngr 'return uid info)))

(defun emigo-epc-handler-called-method (mngr uid name args)
  "[internal] low-level message handler for peer's calling."
  (let ((mngr mngr) (uid uid))
    (let* ((_methods (emigo-epc-manager-methods mngr))
           (method (emigo-epc-manager-get-method mngr name)))
      (cond
       ((null method)
        (emigo-epc-log "ERR: No such method : %s" name)
        (emigo-epc-manager-send mngr 'epc-error uid (format "EPC-ERROR: No such method : %s" name)))
       (t
        (condition-case err
            (let* ((f (emigo-epc-method-task method))
                   (ret (apply f args)))
              (cond
               ((emigo-deferred-object-p ret)
                (emigo-deferred-nextc ret
                                      (lambda (xx) (emigo-epc-manager-send mngr 'return uid xx))))
               (t (emigo-epc-manager-send mngr 'return uid ret))))
          (error
           ;; Include method name and args in error for debugging
           (let ((err-msg (format "FAILED in %s: %S with ERROR: %S" name args err)))
             (emigo-epc-log err-msg)
             (emigo-epc-manager-send mngr 'return-error uid err-msg)))))))))

(defun emigo-epc-manager-remove-session (mngr uid)
  "[internal] Remove a session from the epc manager object."
  (cl-loop with ret = nil
           for pair in (emigo-epc-manager-sessions mngr)
           unless (eq uid (car pair))
           do (push pair ret)
           finally
           do (setf (emigo-epc-manager-sessions mngr) ret)))

(defun emigo-epc-handler-return (mngr uid args)
  "[internal] low-level message handler for normal returns."
  (let ((pair (assq uid (emigo-epc-manager-sessions mngr))))
    (cond
     (pair
      (emigo-epc-log "RET: id:%s [%S]" uid args)
      (emigo-epc-manager-remove-session mngr uid)
      (emigo-deferred-callback (cdr pair) args))
     (t                                 ; error
      (emigo-epc-log "RET: NOT FOUND: id:%s [%S]" uid args)))))

(defun emigo-epc-handler-return-error (mngr uid args)
  "[internal] low-level message handler for application errors."
  (let ((pair (assq uid (emigo-epc-manager-sessions mngr)))
    (cond
     (pair
      (emigo-epc-log "RET-ERR: id:%s [%S]" uid args)
      (emigo-epc-manager-remove-session mngr uid)
      (let* ((err-str (format "%S" args))
        ;; Add context about the failed call if available
        (when (and (listp args) (eq (car args) 'error))
          (setq err-str (format "EPC call failed: %S" args)))
        (emigo-deferred-errorback (cdr pair) err-str))))
     (t                                 ; error
      (emigo-epc-log "RET-ERR: NOT FOUND: id:%s [%S]" uid args))))))

(defun emigo-epc-handler-epc-error (mngr uid args)
  "[internal] low-level message handler for epc errors."
  (let ((pair (assq uid (emigo-epc-manager-sessions mngr))))
    (cond
     (pair
      (emigo-epc-log "RET-EPC-ERR: id:%s [%S]" uid args)
      (emigo-epc-manager-remove-session mngr uid)
      (emigo-deferred-errorback (cdr pair) (list 'epc-error args)))
     (t                                 ; error
      (emigo-epc-log "RET-EPC-ERR: NOT FOUND: id:%s [%S]" uid args)))))

(defun emigo-epc-call-deferred (mngr method-name args)
  "Call peer's method with args asynchronously. Return a deferred
object which is called with the result."
  (let ((uid (emigo-epc-uid))
        (sessions (emigo-epc-manager-sessions mngr))
        (d (emigo-deferred-new)))
    (push (cons uid d) sessions)
    (setf (emigo-epc-manager-sessions mngr) sessions)
    (emigo-epc-manager-send mngr 'call uid method-name args)
    d))

(defun emigo-epc-define-method (mngr method-name task &optional arg-specs docstring)
  "Define a method and return a deferred object which is called by the peer."
  (let* ((method (make-emigo-epc-method
                  :name method-name :task task
                  :arg-specs arg-specs :docstring docstring))
         (methods (cons method (emigo-epc-manager-methods mngr))))
    (setf (emigo-epc-manager-methods mngr) methods)
    method))

(defun emigo-epc-sync (mngr d)
  "Wrap deferred methods with synchronous waiting, and return the result.
If an exception is occurred, this function throws the error."
  (let ((result 'emigo-epc-nothing))
    (emigo-deferred-chain
     d
     (emigo-deferred-nextc it
                           (lambda (x) (setq result x)))
     (emigo-deferred-error it
                           (lambda (er) (setq result (cons 'error er)))))
    (while (eq result 'emigo-epc-nothing)
      (save-current-buffer
        (accept-process-output
         (emigo-epc-connection-process (emigo-epc-manager-connection mngr))
         0 emigo-epc-accept-process-timeout t)))
    (if (and (consp result) (eq 'error (car result)))
        (error (cdr result)) result)))

(defun emigo-epc-call-sync (mngr method-name args)
  "Call peer's method with args synchronously and return the result.
If an exception is occurred, this function throws the error."
  (emigo-epc-sync mngr (emigo-epc-call-deferred mngr method-name args)))

(defun emigo-epc-live-p (mngr)
  "Return non-nil when MNGR is an EPC manager object with a live
connection."
  (let ((proc (ignore-errors
                (emigo-epc-connection-process (emigo-epc-manager-connection mngr)))))
    (and (processp proc)
         ;; Same as `process-live-p' in Emacs >= 24:
         (memq (process-status proc) '(run open listen connect stop)))))

;; epcs
(defvar emigo-epc-server-client-processes nil
  "[internal] A list of ([process object] . [`emigo-epc-manager' instance]).
When the server process accepts the client connection, the
`emigo-epc-manager' instance is created and stored in this variable
`emigo-epc-server-client-processes'. This variable is used for the management
purpose.")

;; emigo-epc-server
;;   name    : process name (string)   ex: "EPC Server 1"
;;   process : server process object
;;   port    : port number
;;   connect-function : initialize function for `emigo-epc-manager' instances
(cl-defstruct emigo-epc-server name process port connect-function)

(defvar emigo-epc-server-processes nil
  "[internal] A list of ([process object] . [`emigo-epc-server' instance]).
This variable is used for the management purpose.")

(defun emigo-epc-server-get-manager-by-process (proc)
  "[internal] Return the emigo-epc-manager instance for the PROC."
  (cl-loop for (pp . mngr) in emigo-epc-server-client-processes
           if (eql pp proc)
           do (cl-return mngr)
           finally return nil))

(defun emigo-epc-server-accept (process)
  "[internal] Initialize the process and return emigo-epc-manager object."
  (emigo-epc-log "EMIGO-EPC-SERVER- >> Connection accept: %S" process)
  (let* ((connection-id (emigo-epc-uid))
         (connection-name (format "emigo-epc con %s" connection-id))
         (channel (list connection-name nil))
         (connection (make-emigo-epc-connection
                      :name connection-name
                      :process process
                      :buffer (process-buffer process)
                      :channel channel)))
    (emigo-epc-log "EMIGO-EPC-SERVER- >> Connection establish")
    (set-process-coding-system process 'binary 'binary)
    (set-process-filter process
                        (lambda (p m)
                          (emigo-epc-process-filter connection p m)))
    (set-process-query-on-exit-flag process nil)
    (set-process-sentinel process
                          (lambda (p e)
                            (emigo-epc-process-sentinel connection p e)))
    (make-emigo-epc-manager :server-process process :port t
                            :connection connection)))

(defun emigo-epc-server-sentinel (process message connect-function)
  "[internal] Process sentinel handler for the server process."
  (emigo-epc-log "EMIGO-EPC-SERVER- SENTINEL: %S %S" process message)
  (let ((mngr (emigo-epc-server-get-manager-by-process process)))
    (cond
     ;; new connection
     ((and (string-match "open" message) (null mngr))
      (condition-case err
          (let ((mngr (emigo-epc-server-accept process)))
            (push (cons process mngr) emigo-epc-server-client-processes)
            (emigo-epc-init-epc-layer mngr)
            (when connect-function (funcall connect-function mngr))
            mngr)
        ('error
         (emigo-epc-log "EMIGO-EPC-SERVER- Protocol error: %S" err)
         (emigo-epc-log "EMIGO-EPC-SERVER- ABORT %S" process)
         (delete-process process))))
     ;; ignore
     ((null mngr) nil )
     ;; disconnect
     (t
      (let ((pair (assq process emigo-epc-server-client-processes)) _d)
        (when pair
          (emigo-epc-log "EMIGO-EPC-SERVER- DISCONNECT %S" process)
          (emigo-epc-stop-epc (cdr pair))
          (setq emigo-epc-server-client-processes
                (assq-delete-all process emigo-epc-server-client-processes))
          ))
      nil))))

(defun emigo-epc-server-start (connect-function &optional port)
  "Start TCP Server and return the main process object."
  (let*
      ((connect-function connect-function)
       (name (format "EMIGO EPC Server %s" (emigo-epc-uid)))
       (buf (emigo-epc-make-procbuf (format " *%s*" name)))
       (main-process
        (make-network-process
         :name name
         :buffer buf
         :family 'ipv4
         :server t
         :host "127.0.0.1"
         :service (or port t)
         :noquery t
         :sentinel
         (lambda (process message)
           (emigo-epc-server-sentinel process message connect-function)))))
    (push (cons main-process
                (make-emigo-epc-server
                 :name name :process main-process
                 :port (process-contact main-process :service)
                 :connect-function connect-function))
          emigo-epc-server-processes)
    main-process))

(provide 'emigo-epc)
;;; emigo-epc.el ends here
