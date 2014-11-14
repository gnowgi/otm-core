# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

from django.contrib.gis.db import models
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from treemap.models import Species, Plot, Tree

from importer.models import GenericImportRow, GenericImportEvent
from importer import fields
from importer import errors


class TreeImportEvent(GenericImportEvent):
    """
    A TreeImportEvent represents an attempt to upload a csv containing
    tree/plot information
    """

    import_type = 'tree'

    plot_length_conversion_factor = models.FloatField(default=1.0)
    plot_width_conversion_factor = models.FloatField(default=1.0)
    diameter_conversion_factor = models.FloatField(default=1.0)
    tree_height_conversion_factor = models.FloatField(default=1.0)
    canopy_height_conversion_factor = models.FloatField(default=1.0)

    def create_row(self, *args, **kwargs):
        return TreeImportRow.objects.create(*args, **kwargs)

    def row_set(self):
        return self.treeimportrow_set

    def __unicode__(self):
        return u"Tree Import #%s" % self.pk

    def validate_main_file(self):
        required_fields = {fields.trees.POINT_X,
                           fields.trees.POINT_Y}

        return self._validate_field_names(fields.trees.ALL, required_fields,
                                          errors.MISSING_POINTS)


class TreeImportRow(GenericImportRow):
    WARNING = 2

    PLOT_MAP = {
        'geom': fields.trees.POINT,
        'width': fields.trees.PLOT_WIDTH,
        'length': fields.trees.PLOT_LENGTH,
        'readonly': fields.trees.READ_ONLY,
        'owner_orig_id': fields.trees.ORIG_ID_NUMBER,
        'address_street': fields.trees.STREET_ADDRESS,
        'address_city': fields.trees.CITY_ADDRESS,
        'address_zip': fields.trees.POSTAL_CODE
    }

    TREE_MAP = {
        'diameter': fields.trees.DIAMETER,
        'height': fields.trees.TREE_HEIGHT,
        'canopy_height': fields.trees.CANOPY_HEIGHT,
        'species': fields.trees.SPECIES_OBJECT,
        'date_planted': fields.trees.DATE_PLANTED,
        'readonly': fields.trees.READ_ONLY
    }

    # plot that was created from this row
    plot = models.ForeignKey(Plot, null=True, blank=True)

    # The main import event
    import_event = models.ForeignKey(TreeImportEvent)

    @property
    def model_fields(self):
        return fields.trees

    def commit_row(self):
        # If this row was already commit... abort
        if self.plot:
            self.status = TreeImportRow.SUCCESS
            self.save()

        # First validate
        if not self.validate_row():
            return False

        # Get our data
        data = self.cleaned

        self.convert_units(data, {
            fields.trees.PLOT_WIDTH:
            self.import_event.plot_width_conversion_factor,

            fields.trees.PLOT_LENGTH:
            self.import_event.plot_length_conversion_factor,

            fields.trees.DIAMETER:
            self.import_event.diameter_conversion_factor,

            fields.trees.TREE_HEIGHT:
            self.import_event.tree_height_conversion_factor,

            fields.trees.CANOPY_HEIGHT:
            self.import_event.canopy_height_conversion_factor
        })

        plot_edited = False
        tree_edited = False

        # Initially grab plot from row if it exists
        plot = self.plot
        if plot is None:
            plot = Plot(instance=self.import_event.instance)

        # Event if TREE_PRESENT is None, a tree
        # can still be spawned here if there is
        # any tree data later
        tree = plot.current_tree()

        # Check for an existing tree:
        if self.model_fields.OPENTREEMAP_ID_NUMBER in data:
            plot = Plot.objects.get(
                pk=data[self.model_fields.OPENTREEMAP_ID_NUMBER])
            tree = plot.current_tree()
        else:
            if data.get(self.model_fields.TREE_PRESENT, False):
                tree_edited = True
                if tree is None:
                    tree = Tree()

        for modelkey, importdatakey in TreeImportRow.PLOT_MAP.iteritems():
            importdata = data.get(importdatakey, None)

            if importdata:
                plot_edited = True
                setattr(plot, modelkey, importdata)

        if plot_edited:
            plot.save_with_system_user_bypass_auth()

        for modelkey, importdatakey in TreeImportRow.TREE_MAP.iteritems():
            importdata = data.get(importdatakey, None)

            if importdata:
                tree_edited = True
                if tree is None:
                    tree = Tree()
                setattr(tree, modelkey, importdata)

        if tree_edited:
            tree.plot = plot
            tree.instance = plot.instance
            tree.save_with_system_user_bypass_auth()

        self.plot = plot
        self.status = TreeImportRow.SUCCESS
        self.save()

        return True

    def validate_geom(self):
        x = self.cleaned.get(fields.trees.POINT_X, None)
        y = self.cleaned.get(fields.trees.POINT_Y, None)

        # Note, this shouldn't really happen since main
        # file validation will fail, but butter safe than sorry
        if x is None or y is None:
            self.append_error(errors.MISSING_POINTS,
                              (fields.trees.POINT_X, fields.trees.POINT_Y))
            return False

        # Simple validation
        # longitude must be between -180 and 180
        # latitude must be betwen -90 and 90
        if abs(x) > 180 or abs(y) > 90:
            self.append_error(errors.INVALID_GEOM,
                              (fields.trees.POINT_X, fields.trees.POINT_Y))
            return False

        p = Point(x, y, srid=4326)
        p.transform(3857)

        if self.import_event.instance.bounds.contains(p):
            self.cleaned[fields.trees.POINT] = p
        else:
            self.append_error(errors.GEOM_OUT_OF_BOUNDS,
                              (fields.trees.POINT_X, fields.trees.POINT_Y))
            return False

        return True

    def validate_otm_id(self):
        oid = self.cleaned.get(fields.trees.OPENTREEMAP_ID_NUMBER, None)
        if oid:
            has_plot = Plot.objects.filter(pk=oid).exists()

            if not has_plot:
                self.append_error(errors.INVALID_OTM_ID,
                                  fields.trees.OPENTREEMAP_ID_NUMBER)
                return False

        return True

    def validate_proximity(self, point):
        plot_ids_from_this_import = TreeImportRow.objects\
            .filter(import_event=self.import_event)\
            .filter(plot__isnull=False)\
            .values_list('plot__pk', flat=True)

        nearby = Plot.objects\
                     .filter(instance=self.import_event.instance)\
                     .filter(geom__distance_lte=(point, D(ft=10.0)))\
                     .distance(point)\
                     .exclude(pk__in=plot_ids_from_this_import)\

        oid = self.cleaned.get(fields.trees.OPENTREEMAP_ID_NUMBER, None)
        if oid:
            nearby = nearby.exclude(pk=oid)

        nearby = nearby.order_by('distance')[:5]

        if len(nearby) > 0:
            self.append_error(errors.NEARBY_TREES,
                              (fields.trees.POINT_X, fields.trees.POINT_Y),
                              [p.pk for p in nearby])
            return False
        else:
            return True

    def validate_species_max(self, field, max_val, err):
        inputval = self.cleaned.get(field, None)
        if inputval:
            if max_val and inputval > max_val:
                self.append_error(err, field, max_val)
                return False

        return True

    def validate_species_dbh_max(self, species):
        return self.validate_species_max(
            fields.trees.DIAMETER,
            species.max_diameter, errors.SPECIES_DBH_TOO_HIGH)

    def validate_species_height_max(self, species):
        return self.validate_species_max(
            fields.trees.TREE_HEIGHT,
            species.max_height, errors.SPECIES_HEIGHT_TOO_HIGH)

    def validate_species(self):
        genus = self.datadict.get(fields.trees.GENUS, '')
        species = self.datadict.get(fields.trees.SPECIES, '')
        cultivar = self.datadict.get(fields.trees.CULTIVAR, '')
        other_part = self.datadict.get(fields.trees.OTHER_PART_OF_NAME, '')

        if genus != '' or species != '' or cultivar != '':
            matching_species = Species.objects \
                .filter(genus__iexact=genus) \
                .filter(species__iexact=species) \
                .filter(cultivar__iexact=cultivar) \
                .filter(other_part_of_name__iexact=other_part)

            if len(matching_species) == 1:
                self.cleaned[fields.trees.SPECIES_OBJECT] = matching_species[0]
            else:
                self.append_error(
                    errors.INVALID_SPECIES, (fields.trees.GENUS,
                                             fields.trees.SPECIES,
                                             fields.trees.CULTIVAR),
                    ' '.join([genus, species, cultivar]).strip())
                return False

        return True

    def validate_row(self):
        """
        Validate a row. Returns True if there were no fatal errors,
        False otherwise

        The method mutates self in two ways:
        - The 'errors' field on self will be appended to
          whenever an error is found
        - The 'cleaned' field on self will be set as fields
          get validated
        """
        # Clear errrors
        self.errors = ''

        # Convert all fields to correct datatypes
        self.validate_and_convert_datatypes()

        # We can work on the 'cleaned' data from here on out
        self.validate_otm_id()

        # Attaches a GEOS point to fields.trees.POINT
        self.validate_geom()

        # This could be None or not set if there was an earlier error
        pt = self.cleaned.get(fields.trees.POINT, None)

        self.validate_species()

        # This could be None or unset if species data were not given
        species = self.cleaned.get(fields.trees.SPECIES_OBJECT, None)

        # These validations are non-fatal
        if species:
            self.validate_species_dbh_max(species)
            self.validate_species_height_max(species)

        if pt:
            self.validate_proximity(pt)

        fatal = False
        if self.has_fatal_error():
            self.status = TreeImportRow.ERROR
            fatal = True
        elif self.has_errors():  # Has 'warning'/tree watch errors
            self.status = TreeImportRow.WARNING
        else:
            self.status = TreeImportRow.VERIFIED

        self.save()
        return not fatal
