from django.db import OperationalError, ProgrammingError, connection, models
from django.utils.translation import get_language, gettext


class Testimonio(models.Model):

    nombre = models.CharField(max_length=120)

    negocio = models.CharField(
        max_length=150,
        blank=True,
        null=True
    )

    negocio_en = models.CharField(
        max_length=150,
        blank=True,
        null=True
    )

    comentario = models.TextField()

    comentario_en = models.TextField(
        blank=True,
        null=True
    )

    estrellas = models.IntegerField(
        default=5
    )

    foto = models.ImageField(
        upload_to="testimonios/",
        blank=True,
        null=True
    )

    orden = models.PositiveIntegerField(
        default=0
    )

    activo = models.BooleanField(
        default=True
    )

    creado = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["orden", "-creado"]

    @property
    def negocio_traducido(self):
        if get_language().startswith("en") and self.negocio_en:
            return self.negocio_en
        return self.negocio

    @property
    def comentario_traducido(self):
        if get_language().startswith("en") and self.comentario_en:
            return self.comentario_en
        return self.comentario

    def __str__(self):
        return f"{self.nombre} - {self.negocio}"


class HomeContenido(models.Model):

    hero_titulo_principal = models.CharField(max_length=120, default="Tu Mayorista de")
    hero_titulo_principal_en = models.CharField(max_length=120, blank=True, null=True)

    hero_titulo_resaltado = models.CharField(max_length=120, default="Productos Latinos")
    hero_titulo_resaltado_en = models.CharField(max_length=120, blank=True, null=True)

    hero_titulo_final = models.CharField(max_length=120, default="de Confianza")
    hero_titulo_final_en = models.CharField(max_length=120, blank=True, null=True)

    hero_subtitulo = models.CharField(
        max_length=220,
        default="Haz tus pedidos de forma rapida y segura. Compras al por mayor.",
    )
    hero_subtitulo_en = models.CharField(max_length=220, blank=True, null=True)

    hero_boton_texto = models.CharField(max_length=80, default="Ver Catalogo")
    hero_boton_texto_en = models.CharField(max_length=80, blank=True, null=True)

    cta_titulo = models.CharField(
        max_length=220,
        default="Tienes una tienda? Solicita tu cuenta mayorista hoy",
    )
    cta_titulo_en = models.CharField(max_length=220, blank=True, null=True)

    cta_boton_registro_texto = models.CharField(max_length=80, default="Crear Cuenta")
    cta_boton_registro_texto_en = models.CharField(max_length=80, blank=True, null=True)

    cta_boton_catalogo_texto = models.CharField(max_length=80, default="Ver Catalogo")
    cta_boton_catalogo_texto_en = models.CharField(max_length=80, blank=True, null=True)

    quienes_titulo = models.CharField(max_length=120, default="Quienes Somos?")
    quienes_titulo_en = models.CharField(max_length=120, blank=True, null=True)

    quienes_descripcion = models.TextField(
        default=(
            "En La Tortilla Grocery LLC, somos el aliado de confianza de los negocios latinos. "
            "Ofrecemos productos y servicios mayoristas de alta calidad, adaptados a sus necesidades. "
            "Nos enfocamos en brindar soluciones eficientes para que su negocio crezca y prospere en un mercado competitivo."
        )
    )
    quienes_descripcion_en = models.TextField(blank=True, null=True)

    beneficio_1_titulo = models.CharField(max_length=120, default="Abastecimiento Inteligente")
    beneficio_1_titulo_en = models.CharField(max_length=120, blank=True, null=True)
    beneficio_1_subtitulo = models.CharField(max_length=160, default="Productos siempre disponibles")
    beneficio_1_subtitulo_en = models.CharField(max_length=160, blank=True, null=True)

    beneficio_2_titulo = models.CharField(max_length=120, default="Logica Eficiente")
    beneficio_2_titulo_en = models.CharField(max_length=120, blank=True, null=True)
    beneficio_2_subtitulo = models.CharField(max_length=160, default="Entrega rapida y segura")
    beneficio_2_subtitulo_en = models.CharField(max_length=160, blank=True, null=True)

    beneficio_3_titulo = models.CharField(max_length=120, default="Relacion a Largo Plazo")
    beneficio_3_titulo_en = models.CharField(max_length=120, blank=True, null=True)
    beneficio_3_subtitulo = models.CharField(max_length=160, default="Compromiso y confianza")
    beneficio_3_subtitulo_en = models.CharField(max_length=160, blank=True, null=True)

    beneficio_4_titulo = models.CharField(max_length=120, default="Crecimiento Sostenible")
    beneficio_4_titulo_en = models.CharField(max_length=120, blank=True, null=True)
    beneficio_4_subtitulo = models.CharField(max_length=160, default="Apoyo para tu expansion")
    beneficio_4_subtitulo_en = models.CharField(max_length=160, blank=True, null=True)

    estadistica_1_valor = models.CharField(max_length=60, default="+100")
    estadistica_1_valor_en = models.CharField(max_length=60, blank=True, null=True)
    estadistica_1_label = models.CharField(max_length=120, default="Negocios Abastecidos")
    estadistica_1_label_en = models.CharField(max_length=120, blank=True, null=True)

    estadistica_2_valor = models.CharField(max_length=60, default="98%")
    estadistica_2_valor_en = models.CharField(max_length=60, blank=True, null=True)
    estadistica_2_label = models.CharField(max_length=120, default="Pedidos Exitosos")
    estadistica_2_label_en = models.CharField(max_length=120, blank=True, null=True)

    estadistica_3_valor = models.CharField(max_length=60, default="+5 Anos")
    estadistica_3_valor_en = models.CharField(max_length=60, blank=True, null=True)
    estadistica_3_label = models.CharField(max_length=120, default="De Experiencia")
    estadistica_3_label_en = models.CharField(max_length=120, blank=True, null=True)

    activo = models.BooleanField(default=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-actualizado"]

    def _translated(self, es_value, en_value):
        if get_language().startswith("en"):
            if en_value:
                return en_value
            return gettext(es_value)
        return es_value

    @property
    def hero_titulo_principal_traducido(self):
        return self._translated(self.hero_titulo_principal, self.hero_titulo_principal_en)

    @property
    def hero_titulo_resaltado_traducido(self):
        return self._translated(self.hero_titulo_resaltado, self.hero_titulo_resaltado_en)

    @property
    def hero_titulo_final_traducido(self):
        return self._translated(self.hero_titulo_final, self.hero_titulo_final_en)

    @property
    def hero_subtitulo_traducido(self):
        return self._translated(self.hero_subtitulo, self.hero_subtitulo_en)

    @property
    def hero_boton_texto_traducido(self):
        return self._translated(self.hero_boton_texto, self.hero_boton_texto_en)

    @property
    def cta_titulo_traducido(self):
        return self._translated(self.cta_titulo, self.cta_titulo_en)

    @property
    def cta_boton_registro_texto_traducido(self):
        return self._translated(self.cta_boton_registro_texto, self.cta_boton_registro_texto_en)

    @property
    def cta_boton_catalogo_texto_traducido(self):
        return self._translated(self.cta_boton_catalogo_texto, self.cta_boton_catalogo_texto_en)

    @property
    def quienes_titulo_traducido(self):
        return self._translated(self.quienes_titulo, self.quienes_titulo_en)

    @property
    def quienes_descripcion_traducido(self):
        return self._translated(self.quienes_descripcion, self.quienes_descripcion_en)

    @property
    def beneficio_1_titulo_traducido(self):
        return self._translated(self.beneficio_1_titulo, self.beneficio_1_titulo_en)

    @property
    def beneficio_1_subtitulo_traducido(self):
        return self._translated(self.beneficio_1_subtitulo, self.beneficio_1_subtitulo_en)

    @property
    def beneficio_2_titulo_traducido(self):
        return self._translated(self.beneficio_2_titulo, self.beneficio_2_titulo_en)

    @property
    def beneficio_2_subtitulo_traducido(self):
        return self._translated(self.beneficio_2_subtitulo, self.beneficio_2_subtitulo_en)

    @property
    def beneficio_3_titulo_traducido(self):
        return self._translated(self.beneficio_3_titulo, self.beneficio_3_titulo_en)

    @property
    def beneficio_3_subtitulo_traducido(self):
        return self._translated(self.beneficio_3_subtitulo, self.beneficio_3_subtitulo_en)

    @property
    def beneficio_4_titulo_traducido(self):
        return self._translated(self.beneficio_4_titulo, self.beneficio_4_titulo_en)

    @property
    def beneficio_4_subtitulo_traducido(self):
        return self._translated(self.beneficio_4_subtitulo, self.beneficio_4_subtitulo_en)

    @property
    def estadistica_1_valor_traducido(self):
        return self._translated(self.estadistica_1_valor, self.estadistica_1_valor_en)

    @property
    def estadistica_1_label_traducido(self):
        return self._translated(self.estadistica_1_label, self.estadistica_1_label_en)

    @property
    def estadistica_2_valor_traducido(self):
        return self._translated(self.estadistica_2_valor, self.estadistica_2_valor_en)

    @property
    def estadistica_2_label_traducido(self):
        return self._translated(self.estadistica_2_label, self.estadistica_2_label_en)

    @property
    def estadistica_3_valor_traducido(self):
        return self._translated(self.estadistica_3_valor, self.estadistica_3_valor_en)

    @property
    def estadistica_3_label_traducido(self):
        return self._translated(self.estadistica_3_label, self.estadistica_3_label_en)

    def __str__(self):
        return f"Home contenido ({'activo' if self.activo else 'inactivo'})"


def ensure_homecontenido_quienes_schema():
    table_name = HomeContenido._meta.db_table
    target_fields = (
        'quienes_titulo',
        'quienes_titulo_en',
        'quienes_descripcion',
        'quienes_descripcion_en',
        'beneficio_1_titulo',
        'beneficio_1_titulo_en',
        'beneficio_1_subtitulo',
        'beneficio_1_subtitulo_en',
        'beneficio_2_titulo',
        'beneficio_2_titulo_en',
        'beneficio_2_subtitulo',
        'beneficio_2_subtitulo_en',
        'beneficio_3_titulo',
        'beneficio_3_titulo_en',
        'beneficio_3_subtitulo',
        'beneficio_3_subtitulo_en',
        'beneficio_4_titulo',
        'beneficio_4_titulo_en',
        'beneficio_4_subtitulo',
        'beneficio_4_subtitulo_en',
        'estadistica_1_valor',
        'estadistica_1_valor_en',
        'estadistica_1_label',
        'estadistica_1_label_en',
        'estadistica_2_valor',
        'estadistica_2_valor_en',
        'estadistica_2_label',
        'estadistica_2_label_en',
        'estadistica_3_valor',
        'estadistica_3_valor_en',
        'estadistica_3_label',
        'estadistica_3_label_en',
    )

    def get_existing_columns():
        with connection.cursor() as cursor:
            return {
                column.name for column in connection.introspection.get_table_description(cursor, table_name)
            }

    schema_updated = False
    existing_columns = get_existing_columns()

    for field_name in target_fields:
        if field_name in existing_columns:
            continue

        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.add_field(HomeContenido, HomeContenido._meta.get_field(field_name))
            schema_updated = True
        except (OperationalError, ProgrammingError) as exc:
            if field_name not in get_existing_columns():
                raise exc
        existing_columns = get_existing_columns()

    return schema_updated