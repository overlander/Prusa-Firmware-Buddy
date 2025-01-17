#pragma once

/**
 * \file
 * \brief Tracking the state of gcode upload and parsing whatever needed.
 *
 * This integrates into the post handling in our http server (yes, that's kind
 * of wrong as abstractions go, and this'll need to change eventually, probably
 * as we replace the http server). It is also kind of single-purpose parser,
 * since we want to have only a single POST endpoint handling
 * multipart/form-data (the others will be JSON, which'll be some other kind of
 * beast anyway).
 *
 * Each new upload creates an instance, feeds it with data and checks if
 * everything goes fine (the errors can happen anytime and it is up to the
 * caller to either check after each feeding or at the end).
 *
 * Internally, it calls the callbacks from the http handlers. If it calls the
 * start handler, it guarantees it'll either see an error returned from one of
 * the callbacks (in which case it is assumed the callbacks clean up their
 * state) or will eventually call the finish callback (even if the upload is
 * interrupted, reaches invalid state, etc).
 *
 * The data fed to it may be split arbitrarily.
 */

#include "handler.h"

#ifdef __cplusplus
extern "C" {
#endif

/// A handle to the upload state.
struct Uploader;

/**
 * \brief Creates a fresh new instance of the tracker.
 *
 * \param boundary C-style string specifying the boundary between parts. From
 *   the Content-Type header. Can be freed after the call to this function.
 * \param handlers Structure with callbacks to pass the parsed data to. Needs
 *   to stay alive until the call of uploader_finish, but the uploader doesn't
 *   take ownership.
 * \return
 *   - A fresh instance of the tracker. Note that even this one might already
 *     be in an error state and it might be worth checking for errors.
 *   - NULL in case of allocation errors.
 */
struct Uploader *uploader_init(const char *boundary, struct HttpHandlers *handlers);

/**
 * \brief Inserts more data into the tracker.
 *
 * This assumes the data is the next chunk of valid multipart/form-data with
 * the upload "form". Internally it might call some of the callbacks.
 *
 * \param uploader An instance of the uploader.
 * \param data, len The data to process (and their length). The uploader does
 *   not take ownership and the data may be disposed of right after the call
 *   terminates (they are not kept around).
 */
void uploader_feed(struct Uploader *uploader, const char *data, size_t len);

/**
 * \brief Finishes processing and releases resources.
 *
 * This finishes any needed processing and calling of callbacks. Then it frees
 * internal resources. After this call, the uploader is no longer valid.
 *
 * It is recommended to check for errors before calling this.
 *
 * \param uploader The uploader to free.
 * \return Boolean specifying if an end of the data has been seen. Result of
 *   false either means the form didn't contain all the needed parts or that the
 *   upload was aborted/interrupted and the "tail" is missing.
 */
bool uploader_finish(struct Uploader *uploader);

/**
 * \brief Checks for error state of the uploader.
 *
 * Returns if there was an error in processing the upload (and which one). Once
 * the uploader reaches an error state, there's no way to reset it - errors are
 * not recoverable.
 *
 * It is possible to check repeatedly/after each feeding or at the end. Feeding
 * data to an uploader in error state is possible, but has no effect.
 *
 * \return
 *   - 0 if there was no error so far.
 *   - Anything else: http status corresponding to the error.
 */
uint16_t uploader_error(const struct Uploader *uploader);

#ifdef __cplusplus
}
#endif
