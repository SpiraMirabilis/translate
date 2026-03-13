<?php
/**
 * Plugin Name: Fictioneer REST Meta
 * Description: Custom REST endpoints for T9 to manage Fictioneer stories and chapters.
 * Version: 2.1.0
 * Author: T9 Translation App
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

add_action( 'rest_api_init', 'fictioneer_rest_meta_register_routes' );

/**
 * Aggressively invalidate all Fictioneer caches for a story post.
 *
 * Fictioneer's own fictioneer_refresh_post_caches() is blocked during REST
 * requests (fictioneer_multi_save_guard returns true when REST_REQUEST is
 * defined), so we must call the cache-busting functions directly.
 */
function fictioneer_rest_meta_purge_story_caches( $story_id ) {
    // 1. Clear Fictioneer's cached story data
    delete_post_meta( $story_id, 'fictioneer_story_data_collection' );
    delete_post_meta( $story_id, 'fictioneer_story_chapter_index_html' );

    // 2. WordPress object cache
    clean_post_cache( $story_id );

    // 3. Fictioneer's HTML file cache
    if ( function_exists( 'fictioneer_clear_cached_content' ) ) {
        fictioneer_clear_cached_content( $story_id );
    }

    // 4. Purge shortcode transients (story listings, latest chapters, etc.)
    if ( function_exists( 'fictioneer_delete_transients_like' ) ) {
        fictioneer_delete_transients_like( 'fictioneer_shortcode' );
    }

    // 5. Clear stories total word count transient
    delete_transient( 'fictioneer_stories_total_word_count' );

    // 5b. Clear the chapter list HTML transient (cached for 24h by Fictioneer)
    delete_transient( 'fictioneer_story_chapter_list_html' . $story_id );

    // 6. Fictioneer's own post cache purge (handles WP Rocket, LiteSpeed, etc.)
    if ( function_exists( 'fictioneer_purge_post_cache' ) ) {
        fictioneer_purge_post_cache( $story_id );
    }

    // 7. Purge template caches (story/chapter list pages)
    if ( function_exists( 'fictioneer_purge_template_caches' ) ) {
        fictioneer_purge_template_caches( 'stories' );
        fictioneer_purge_template_caches( 'chapters' );
    }

    // 8. Purge front page cache if set
    $front_page_id = intval( get_option( 'page_on_front' ) ?: -1 );
    if ( $front_page_id > 0 && $front_page_id != $story_id ) {
        clean_post_cache( $front_page_id );
        if ( function_exists( 'fictioneer_purge_post_cache' ) ) {
            fictioneer_purge_post_cache( $front_page_id );
        }
    }
}

function fictioneer_rest_meta_register_routes() {
    // POST /wp-json/t9/v1/chapter/{id}/link-story — set chapter's parent story
    register_rest_route( 't9/v1', '/chapter/(?P<id>\d+)/link-story', [
        'methods'             => 'POST',
        'callback'            => 'fictioneer_rest_meta_link_chapter',
        'permission_callback' => function() {
            return current_user_can( 'edit_posts' );
        },
        'args' => [
            'story_id' => [
                'required' => true,
                'type'     => 'integer',
            ],
            'group' => [
                'required' => false,
                'type'     => 'string',
                'default'  => '',
            ],
        ],
    ] );

    // POST /wp-json/t9/v1/story/{id}/set-chapters — set ordered chapter list
    register_rest_route( 't9/v1', '/story/(?P<id>\d+)/set-chapters', [
        'methods'             => 'POST',
        'callback'            => 'fictioneer_rest_meta_set_chapters',
        'permission_callback' => function() {
            return current_user_can( 'edit_posts' );
        },
        'args' => [
            'chapter_ids' => [
                'required' => true,
                'type'     => 'array',
                'items'    => [ 'type' => 'integer' ],
            ],
        ],
    ] );

    // POST /wp-json/t9/v1/story/{id}/recalculate-words — fix word counts on all chapters
    register_rest_route( 't9/v1', '/story/(?P<id>\d+)/recalculate-words', [
        'methods'             => 'POST',
        'callback'            => 'fictioneer_rest_meta_recalculate_words',
        'permission_callback' => function() {
            return current_user_can( 'edit_posts' );
        },
    ] );

    // POST /wp-json/t9/v1/story/{id}/set-meta — set story meta fields
    register_rest_route( 't9/v1', '/story/(?P<id>\d+)/set-meta', [
        'methods'             => 'POST',
        'callback'            => 'fictioneer_rest_meta_set_story_meta',
        'permission_callback' => function() {
            return current_user_can( 'edit_posts' );
        },
        'args' => [
            'status' => [ 'type' => 'string', 'required' => false ],
            'rating' => [ 'type' => 'string', 'required' => false ],
            'short_description' => [ 'type' => 'string', 'required' => false ],
        ],
    ] );
}

/**
 * Link a chapter to a story via update_post_meta.
 */
function fictioneer_rest_meta_link_chapter( WP_REST_Request $request ) {
    $chapter_id = (int) $request['id'];
    $story_id   = (int) $request['story_id'];
    $group      = sanitize_text_field( $request['group'] ?? '' );

    $chapter = get_post( $chapter_id );
    if ( ! $chapter || $chapter->post_type !== 'fcn_chapter' ) {
        return new WP_Error( 'not_found', 'Chapter not found.', [ 'status' => 404 ] );
    }

    $story = get_post( $story_id );
    if ( ! $story || $story->post_type !== 'fcn_story' ) {
        return new WP_Error( 'not_found', 'Story not found.', [ 'status' => 404 ] );
    }

    // Set the chapter's parent story (Fictioneer stores as string)
    update_post_meta( $chapter_id, 'fictioneer_chapter_story', strval( $story_id ) );

    if ( $group !== '' ) {
        update_post_meta( $chapter_id, 'fictioneer_chapter_group', $group );
    }

    // Calculate and store word count (Fictioneer's save_post hook doesn't fire via REST)
    $content = $chapter->post_content;
    $word_count = str_word_count( wp_strip_all_tags( $content ) );
    update_post_meta( $chapter_id, '_word_count', $word_count );
    update_post_meta( $chapter_id, 'fictioneer_chapter_words', $word_count );

    // Use Fictioneer's own function to append to the story's chapter list
    if ( function_exists( 'fictioneer_append_chapter_to_story' ) ) {
        fictioneer_append_chapter_to_story( $chapter_id, $story_id, true );
    } else {
        // Fallback: manually append
        $chapters = get_post_meta( $story_id, 'fictioneer_story_chapters', true );
        if ( ! is_array( $chapters ) ) {
            $chapters = [];
        }
        if ( ! in_array( $chapter_id, $chapters ) ) {
            $chapters[] = $chapter_id;
            update_post_meta( $story_id, 'fictioneer_story_chapters', $chapters );
        }
    }

    // Purge caches (REST_REQUEST blocks the normal Fictioneer hooks)
    fictioneer_rest_meta_purge_story_caches( $story_id );

    return [ 'status' => 'ok', 'chapter_id' => $chapter_id, 'story_id' => $story_id ];
}

/**
 * Set the ordered chapter list on a story.
 */
function fictioneer_rest_meta_set_chapters( WP_REST_Request $request ) {
    $story_id    = (int) $request['id'];
    $chapter_ids = array_map( 'intval', $request['chapter_ids'] );

    $story = get_post( $story_id );
    if ( ! $story || $story->post_type !== 'fcn_story' ) {
        return new WP_Error( 'not_found', 'Story not found.', [ 'status' => 404 ] );
    }

    update_post_meta( $story_id, 'fictioneer_story_chapters', $chapter_ids );
    update_post_meta( $story_id, 'fictioneer_chapters_modified', current_time( 'mysql' ) );

    // Aggressively purge all Fictioneer caches (REST_REQUEST blocks the normal hooks)
    fictioneer_rest_meta_purge_story_caches( $story_id );

    return [ 'status' => 'ok', 'chapter_count' => count( $chapter_ids ) ];
}

/**
 * Recalculate word counts for all chapters in a story.
 */
function fictioneer_rest_meta_recalculate_words( WP_REST_Request $request ) {
    $story_id = (int) $request['id'];

    $story = get_post( $story_id );
    if ( ! $story || $story->post_type !== 'fcn_story' ) {
        return new WP_Error( 'not_found', 'Story not found.', [ 'status' => 404 ] );
    }

    $chapter_ids = get_post_meta( $story_id, 'fictioneer_story_chapters', true );
    if ( ! is_array( $chapter_ids ) ) {
        return [ 'status' => 'ok', 'updated' => 0 ];
    }

    $updated = 0;
    $total_words = 0;
    foreach ( $chapter_ids as $chapter_id ) {
        $chapter = get_post( (int) $chapter_id );
        if ( ! $chapter || $chapter->post_type !== 'fcn_chapter' ) continue;

        $word_count = str_word_count( wp_strip_all_tags( $chapter->post_content ) );
        update_post_meta( $chapter_id, '_word_count', $word_count );
        update_post_meta( $chapter_id, 'fictioneer_chapter_words', $word_count );
        $total_words += $word_count;
        $updated++;
    }

    // Purge caches (REST_REQUEST blocks the normal Fictioneer hooks)
    fictioneer_rest_meta_purge_story_caches( $story_id );

    return [ 'status' => 'ok', 'updated' => $updated, 'total_words' => $total_words ];
}

/**
 * Set story meta fields (status, rating, short description).
 */
function fictioneer_rest_meta_set_story_meta( WP_REST_Request $request ) {
    $story_id = (int) $request['id'];

    $story = get_post( $story_id );
    if ( ! $story || $story->post_type !== 'fcn_story' ) {
        return new WP_Error( 'not_found', 'Story not found.', [ 'status' => 404 ] );
    }

    if ( $request->has_param( 'status' ) ) {
        update_post_meta( $story_id, 'fictioneer_story_status', sanitize_text_field( $request['status'] ) );
    }
    if ( $request->has_param( 'rating' ) ) {
        update_post_meta( $story_id, 'fictioneer_story_rating', sanitize_text_field( $request['rating'] ) );
    }
    if ( $request->has_param( 'short_description' ) ) {
        update_post_meta( $story_id, 'fictioneer_story_short_description', sanitize_text_field( $request['short_description'] ) );
    }

    // Purge caches (REST_REQUEST blocks the normal Fictioneer hooks)
    fictioneer_rest_meta_purge_story_caches( $story_id );

    return [ 'status' => 'ok' ];
}
