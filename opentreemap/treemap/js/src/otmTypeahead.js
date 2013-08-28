"use strict";

var _ = require("underscore");
// A wrapper around twitter's typeahead library with some sane defaults

require('typeahead');

var $ = require("jquery"),
    mustache = require("mustache"),
    Bacon = require("baconjs"),
    keyCodeIs = require("./baconUtils").keyCodeIs;

function setTypeahead($typeahead, val) {
    $typeahead.typeahead('setQuery', val);
}

function setTypeaheadAfterDataLoaded($typeahead, key, query) {
    if (!key) {
        setTypeahead($typeahead, query);
    } else if (query && query.length !== 0) {
        var data = _.filter(
            $typeahead.data('ttView').datasets[0].itemHash,
            function(data) {
                return data.datum[key] == query;
            });

        if (data.length > 0) {
            setTypeahead($typeahead, data[0].value);
        }
    } else {
        setTypeahead($typeahead, '');
    }
}

exports.getDatum = function($typeahead) {
    return $typeahead.data('datum');
};

exports.create = function(options) {
    var config = options.config,
        template = mustache.compile($(options.template).html()),
        $input = $(options.input),
        $hidden_input = $(options.hidden),
        reverse = options.reverse;

    $input.typeahead({
        name: options.name, // Used for caching
        prefetch: {
            url: options.url,
            ttl: 0 // TODO: Use high TTL and invalidate cache on change
        },
        limit: 10,
        template: template
    });

    var selectStream = $input.asEventStream('typeahead:selected typeahead:autocompleted',
                                            function(e, datum) { return datum; }),

        backspaceOrDeleteStream = $input.asEventStream('keyup')
                                        .filter(keyCodeIs([8, 46])),

        editStream = selectStream.merge(backspaceOrDeleteStream.map(undefined)),

        idStream = selectStream.map(".id")
                                .merge(backspaceOrDeleteStream.map(""));


    editStream.onValue($input, "data", "datum");

    if (options.hidden) {
        idStream.onValue($hidden_input, "val");


        // Specify a 'reverse' key to lookup data in reverse,
        // otherwise restore verbatim
        $input.asEventStream('typeahead:initialized')
            .onValue(function () {
                var value = $hidden_input.val();
                if (value) {
                    setTypeaheadAfterDataLoaded($input, reverse, value);
                }
            });



        $hidden_input.on('restore', function(event, value) {
            // If we're already loaded, this applies right away
            setTypeaheadAfterDataLoaded($input, reverse, value);

            // If we're not, this will get used when loaded later
            $hidden_input.val(value || '');
        });

    }
};
