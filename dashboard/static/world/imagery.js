/* Adapted from God's Eye View src/maps/imagery.js at
 * e7707d9a0f34d9fbffc300023c319f95caa5be30. Copyright (c) 2026 Bilawal Sidhu.
 * MIT license: UPSTREAM-LICENSE.txt. Browser-global adapter; ion sources omitted.
 */
window.ApexWorldImagery = {
  streets: () => new Cesium.OpenStreetMapImageryProvider({
    url: 'https://tile.openstreetmap.org/', credit: '© OpenStreetMap contributors',
  }),
  satellite: () => Cesium.ArcGisMapServerImageryProvider.fromUrl(
    'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer', {
      credit: 'Powered by Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
      enablePickFeatures: false,
    }),
};
